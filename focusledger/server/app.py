"""Loopback-only HTTP server that serves the live report UI.

The server holds no derived state: every ``/api/report`` request re-reads the
raw events out of SQLite and re-derives the timelines, so a browser tab left
open keeps showing current data while the agent is running.

It binds to the loopback interface only. This data is a personal attention
record and must never be reachable from the network.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from focusledger.agent.heartbeat import heartbeat_stale_after, latest_heartbeat
from focusledger.config import AppConfig
from focusledger.db.connection import connect
from focusledger.services import analyst, changes
from focusledger.services import llm as llm_service
from focusledger.services.attention import AttentionPolicy
from focusledger.services.report import build_report_data
from focusledger.services.retention import pending_archive_dates
from focusledger.services.storage import storage_summary

LOGGER = logging.getLogger(__name__)

STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 64 * 1024
MAX_QUESTION_CHARS = 1000
MAX_ANSWER_CHARS = 6000
MODEL_NAME = re.compile(r"^[A-Za-z0-9][\w.:/-]{0,99}$")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class ReportServer(ThreadingHTTPServer):
    """HTTP server carrying the app config for its handlers."""

    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port another one
    # is already listening on, and requests then reach either of them.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: tuple[str, int], config: AppConfig, initial_day: date):
        self.config = config
        self.initial_day = initial_day
        self.policy = AttentionPolicy.from_config(config)
        self.analyst_jobs = AnalystJobs(config)
        super().__init__(address, ReportRequestHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}/"


class AnalystBusy(RuntimeError):
    """Another question is still being answered."""


class AnalystJobs:
    """Answers analyst questions in the background, one at a time.

    An answer takes tens of seconds, so the page starts a job and polls it,
    which also lets it show each tool call as it happens. Only one job runs at
    once: the local model shares one GPU. The last few jobs are kept so a
    reloaded page can still fetch a finished answer.
    """

    KEEP = 20

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, object]] = {}
        self._proposals: dict[str, tuple[changes.Proposal, dict[str, object]]] = {}
        self._running: str | None = None

    def start(self, question: str, model: str | None, think: bool | None,
              history: list[dict[str, str]] | None = None) -> dict[str, object]:
        with self._lock:
            if self._running:
                raise AnalystBusy(self._running)
            job_id = uuid.uuid4().hex[:12]
            self._jobs[job_id] = {
                "id": job_id,
                "status": "running",
                "question": question,
                "model": model or analyst.analyst_model(self._config),
                "tool_calls": [],
                "thinking": [],
                "proposals": [],
                "started": time.time(),
            }
            for old in list(self._jobs)[:-self.KEEP]:
                for state in self._jobs[old]["proposals"]:  # type: ignore[union-attr]
                    self._proposals.pop(str(state["id"]), None)
                del self._jobs[old]
            self._running = job_id
        threading.Thread(
            target=self._run, args=(job_id, question, model, think, history or []), daemon=True
        ).start()
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            snapshot = {k: v for k, v in job.items() if k not in ("started", "finished")}
            for key in ("tool_calls", "thinking"):
                snapshot[key] = list(job[key])  # type: ignore[arg-type]
            snapshot["proposals"] = [dict(state) for state in job["proposals"]]  # type: ignore[union-attr]
            end = job.get("finished") or time.time()
            snapshot["elapsed_seconds"] = round(float(end) - float(job["started"]), 1)  # type: ignore[arg-type]
            return snapshot

    def decide(self, proposal_id: str, approve: bool) -> dict[str, object]:
        """Apply or dismiss a proposal; each one can be decided only once."""
        with self._lock:
            entry = self._proposals.get(proposal_id)
            if entry is None:
                raise LookupError(proposal_id)
            proposal, state = entry
            if state["status"] != "pending":
                raise changes.ChangeError(f"this change is already {state['status']}")
            state["status"] = "applying" if approve else "dismissed"
        if not approve:
            return dict(state)
        try:
            with closing(connect(self._config.database_path)) as conn:
                change = changes.apply(conn, self._config, proposal)
        except Exception:
            with self._lock:
                state["status"] = "pending"
            raise
        with self._lock:
            state.update(status="applied", change_id=change["id"])
            return dict(state)

    def undo(self, change_id: str) -> dict[str, object]:
        with closing(connect(self._config.database_path)) as conn:
            change = changes.undo(conn, self._config, change_id)
        with self._lock:
            for _, state in self._proposals.values():
                if state.get("change_id") == change_id:
                    state["status"] = "undone"
        return change

    def _record_proposal(self, job_id: str, proposal: changes.Proposal) -> None:
        state = {
            "id": proposal.id,
            "action": proposal.action,
            "summary": proposal.summary,
            "preview": proposal.preview,
            "status": "pending",
        }
        with self._lock:
            self._jobs[job_id]["proposals"].append(state)  # type: ignore[union-attr]
            self._proposals[proposal.id] = (proposal, state)

    def _record_thinking(self, job_id: str, thought: str) -> None:
        with self._lock:
            self._jobs[job_id]["thinking"].append(thought)  # type: ignore[union-attr]

    def _record(self, job_id: str, call: analyst.ToolCall) -> None:
        with self._lock:
            self._jobs[job_id]["tool_calls"].append({  # type: ignore[union-attr]
                "name": call.name,
                "arguments": call.arguments,
                "seconds": round(call.seconds, 2),
                "result": call.result,
            })

    def _run(self, job_id: str, question: str, model: str | None, think: bool | None,
             history: list[dict[str, str]]) -> None:
        update: dict[str, object]
        try:
            with closing(connect(self._config.database_path)) as conn:
                result = analyst.analyze(
                    conn, self._config, question, model=model, think=think, history=history,
                    on_tool_call=lambda call: self._record(job_id, call),
                    on_thinking=lambda thought: self._record_thinking(job_id, thought),
                    on_proposal=lambda proposal: self._record_proposal(job_id, proposal),
                )
            update = {
                "status": "done",
                "model": result.model,
                "answer": result.answer,
                "stop_reason": result.stop_reason,
                "tool_mode": result.tool_mode,
                "rounds": result.rounds,
                "corrections": result.corrections,
                "ungrounded_numbers": result.ungrounded_numbers,
            }
        except llm_service.LlmUnavailable as exc:
            update = {"status": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("analyst job failed")
            update = {"status": "error", "error": f"{exc.__class__.__name__}: {exc}"}
        with self._lock:
            self._jobs[job_id].update(update, finished=time.time())
            self._running = None


class ReportRequestHandler(BaseHTTPRequestHandler):
    server_version = "FocusLedger"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # --- routing -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route.startswith("/api/") and not self._host_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "unexpected Host header"})
            elif route == "/":
                self._serve_static("index.html")
            elif route == "/api/report":
                self._serve_report(parse_qs(parsed.query))
            elif route == "/api/status":
                self._serve_status()
            elif route == "/api/days":
                self._serve_days()
            elif route == "/api/storage":
                self._serve_storage()
            elif route == "/api/analyst/models":
                self._serve_analyst_models()
            elif route == "/api/changes":
                self._serve_changes()
            elif route.startswith("/api/analyst/jobs/"):
                self._serve_analyst_job(route[len("/api/analyst/jobs/"):])
            elif route.startswith("/static/"):
                self._serve_static(route[len("/static/"):])
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except BrokenPipeError:
            # The browser navigated away mid-response; nothing to recover.
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        route = urlparse(self.path).path
        try:
            if not self._host_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "unexpected Host header"})
            elif route == "/api/analyst/ask":
                self._start_analyst_job()
            elif route.startswith("/api/analyst/proposals/"):
                self._decide_proposal(route[len("/api/analyst/proposals/"):])
            elif route.startswith("/api/changes/") and route.endswith("/undo"):
                self._undo_change(route[len("/api/changes/"):-len("/undo")])
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _host_allowed(self) -> bool:
        """Only answer API calls addressed to this loopback server.

        A web page can point its own domain at 127.0.0.1 (DNS rebinding) and
        then read responses as same-origin. Its requests still carry that
        domain in Host, so checking Host keeps the API private to pages that
        were really loaded from this address.
        """
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    # --- handlers ------------------------------------------------------
    def _serve_report(self, query: dict[str, list[str]]) -> None:
        raw_date = (query.get("date") or ["today"])[0]
        try:
            day = _resolve_day(raw_date)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid date"})
            return

        config = self.server.config  # type: ignore[attr-defined]
        with closing(connect(config.database_path)) as conn:
            payload = build_report_data(
                conn,
                day,
                config.poll_interval_seconds,
                self.server.policy,  # type: ignore[attr-defined]
                config,
            )
            payload["agent"] = _agent_status(conn, config)
        payload["meta"]["live"] = raw_date == "today"  # type: ignore[index]
        self._send_json(HTTPStatus.OK, payload)

    def _serve_status(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        with closing(connect(config.database_path)) as conn:
            self._send_json(HTTPStatus.OK, _agent_status(conn, config))

    def _serve_days(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        with closing(connect(config.database_path)) as conn:
            dates = analyst.tracked_dates(conn)
        self._send_json(HTTPStatus.OK, {
            "dates": dates,
            "today": date.today().isoformat(),
            "initial": self.server.initial_day.isoformat(),  # type: ignore[attr-defined]
        })

    def _serve_storage(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        with closing(connect(config.database_path)) as conn:
            self._send_json(HTTPStatus.OK, storage_summary(conn, config))

    def _serve_analyst_models(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        payload: dict[str, object] = {
            "default": analyst.analyst_model(config),
            "enabled": bool(config.llm_enabled),
        }
        try:
            payload["models"] = list(llm_service.installed_models(config))
            payload["available"] = bool(config.llm_enabled)
        except llm_service.LlmUnavailable as exc:
            payload.update(models=[], available=False, detail=str(exc))
        if not config.llm_enabled:
            payload["detail"] = "llm_enabled is false in config"
        self._send_json(HTTPStatus.OK, payload)

    def _serve_analyst_job(self, job_id: str) -> None:
        job = self.server.analyst_jobs.get(job_id)  # type: ignore[attr-defined]
        if job is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such job"})
        else:
            self._send_json(HTTPStatus.OK, job)

    def _serve_changes(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        with closing(connect(config.database_path)) as conn:
            self._send_json(HTTPStatus.OK, {"changes": changes.list_changes(conn)})

    def _read_json_body(self) -> dict[str, object] | None:
        """The JSON object in the request, or None after sending the error."""
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            # A plain form post from another site cannot set this type
            # without a CORS preflight, which this server never grants.
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send JSON"})
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return None
        return body

    def _decide_proposal(self, proposal_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        decision = body.get("decision")
        if decision not in ("approve", "dismiss"):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "decision must be approve or dismiss"})
            return
        try:
            state = self.server.analyst_jobs.decide(  # type: ignore[attr-defined]
                proposal_id, approve=decision == "approve"
            )
        except LookupError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such proposal"})
            return
        except changes.ChangeError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, state)

    def _undo_change(self, change_id: str) -> None:
        if self._read_json_body() is None:
            return
        try:
            change = self.server.analyst_jobs.undo(change_id)  # type: ignore[attr-defined]
        except changes.ChangeError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, change)

    def _start_analyst_job(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        question = str(body.get("question") or "").strip() if isinstance(body, dict) else ""
        model = body.get("model")
        think = body.get("think")
        history = body.get("history") or []
        valid_history = isinstance(history, list) and len(history) <= analyst.MAX_HISTORY_TURNS and all(
            isinstance(turn, dict)
            and isinstance(turn.get("question"), str) and len(turn["question"]) <= MAX_QUESTION_CHARS
            and isinstance(turn.get("answer"), str) and len(turn["answer"]) <= MAX_ANSWER_CHARS
            for turn in history
        )
        if not valid_history:
            self._send_json(HTTPStatus.BAD_REQUEST, {
                "error": f"history must be up to {analyst.MAX_HISTORY_TURNS} "
                         "{question, answer} turns",
            })
            return
        if not question or len(question) > MAX_QUESTION_CHARS:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"question must be 1-{MAX_QUESTION_CHARS} characters"},
            )
            return
        if model is not None and not (isinstance(model, str) and MODEL_NAME.match(model)):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid model name"})
            return
        if think is not None and not isinstance(think, bool):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "think must be true, false or null"})
            return
        if model is not None:
            try:
                installed = llm_service.installed_models(self.server.config)  # type: ignore[attr-defined]
            except llm_service.LlmUnavailable as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
                return
            if model not in installed:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"model is not installed: {model}"})
                return
        try:
            job = self.server.analyst_jobs.start(  # type: ignore[attr-defined]
                question, model or None, think,
                [{"question": t["question"], "answer": t["answer"]} for t in history],
            )
        except AnalystBusy as busy:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "another question is still being answered", "job": str(busy)},
            )
            return
        self._send_json(HTTPStatus.ACCEPTED, job)

    def _serve_static(self, relative: str) -> None:
        target = (STATIC_ROOT / relative).resolve()
        # Path traversal guard: the resolved file must stay under STATIC_ROOT.
        if not target.is_file() or STATIC_ROOT not in target.parents:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        body = target.read_bytes()
        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The UI is regenerated per request; never let a browser cache it.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, fmt: str, *args) -> None:
        """Silence per-request logging; the CLI prints its own summary."""
        return


def _resolve_day(raw: str) -> date:
    """Resolve a date parameter, keeping ``today`` live across midnight."""
    if raw == "today":
        return date.today()
    return date.fromisoformat(raw)


def _agent_status(conn, config: AppConfig) -> dict[str, object]:
    heartbeat = latest_heartbeat(conn)
    pending = len(pending_archive_dates(conn, config.raw_retention_days))
    if not heartbeat:
        return {"status": "unknown", "stale": True, "age_seconds": None,
                "pending_archive_days": pending}
    timestamp = str(heartbeat["timestamp"])
    try:
        moment = datetime.fromisoformat(timestamp)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - moment).total_seconds()
    except ValueError:
        age = None
    stale_after = heartbeat_stale_after(config.poll_interval_seconds)
    return {
        "status": str(heartbeat["status"]),
        "timestamp": timestamp,
        "age_seconds": age,
        "stale": bool(heartbeat["status"] != "running" or (age is not None and age > stale_after)),
        "pid": heartbeat.get("pid"),
        "poll_interval_seconds": config.poll_interval_seconds,
        "pending_archive_days": pending,
    }


def create_server(config: AppConfig, day: date, host: str = "127.0.0.1", port: int = 0) -> ReportServer:
    """Bind a report server. ``port=0`` lets the OS pick a free port."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("report server only binds to the loopback interface")
    return ReportServer((host, port), config, day)


def serve_forever(server: ReportServer) -> None:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
