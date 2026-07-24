"""Loopback-only HTTP server that serves the live report UI.

The server holds no derived state: every ``/api/report`` request re-reads the
raw events out of SQLite and re-derives the timelines, so a browser tab left
open keeps showing current data while the agent is running.

It binds to the loopback interface only. This data is a personal attention
record and must never be reachable from the network.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from focusledger.agent.heartbeat import latest_heartbeat
from focusledger.config import AppConfig
from focusledger.db.connection import connect
from focusledger.services.attention import AttentionPolicy
from focusledger.services.report import build_report_data

STATIC_ROOT = Path(__file__).resolve().parent / "static"

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
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: AppConfig, initial_day: date):
        self.config = config
        self.initial_day = initial_day
        self.policy = AttentionPolicy.from_config(config)
        super().__init__(address, ReportRequestHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}/"


class ReportRequestHandler(BaseHTTPRequestHandler):
    server_version = "FocusLedger"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # --- routing -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route == "/":
                self._serve_static("index.html")
            elif route == "/api/report":
                self._serve_report(parse_qs(parsed.query))
            elif route == "/api/status":
                self._serve_status()
            elif route.startswith("/static/"):
                self._serve_static(route[len("/static/"):])
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except BrokenPipeError:
            # The browser navigated away mid-response; nothing to recover.
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    # --- handlers ------------------------------------------------------
    def _serve_report(self, query: dict[str, list[str]]) -> None:
        raw_date = (query.get("date") or ["today"])[0]
        try:
            day = _resolve_day(raw_date)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid date"})
            return

        config = self.server.config  # type: ignore[attr-defined]
        with connect(config.database_path) as conn:
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
        with connect(config.database_path) as conn:
            self._send_json(HTTPStatus.OK, _agent_status(conn, config))

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
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
    if not heartbeat:
        return {"status": "unknown", "stale": True, "age_seconds": None}
    timestamp = str(heartbeat["timestamp"])
    try:
        moment = datetime.fromisoformat(timestamp)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - moment).total_seconds()
    except ValueError:
        age = None
    stale_after = max(config.poll_interval_seconds * 3, 30)
    return {
        "status": str(heartbeat["status"]),
        "timestamp": timestamp,
        "age_seconds": age,
        "stale": bool(heartbeat["status"] != "running" or (age is not None and age > stale_after)),
        "pid": heartbeat.get("pid"),
        "poll_interval_seconds": config.poll_interval_seconds,
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
