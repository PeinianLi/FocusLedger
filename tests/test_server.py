from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from focusledger.config import build_default_config, save_config
from focusledger.db.connection import connect
from focusledger.db.migrations import SCHEMA_VERSION, apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.server.app import create_server
from focusledger.services import analyst as analyst_service
from focusledger.services import changes
from focusledger.services import llm as llm_service
from focusledger.services.activity import record_activity_snapshot


class ServerTestCase(unittest.TestCase):
    """Spins up a real loopback server against a throwaway database."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.config = build_default_config(root)
        save_config(self.config)
        conn = connect(self.config.database_path)
        apply_migrations(conn)
        base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        for i in range(30):
            record_activity_snapshot(
                conn,
                device_id="dev",
                snapshot=ActiveWindowSnapshot(
                    captured_at=base + timedelta(seconds=i * 5),
                    process_id=100,
                    process_name="PyCharm",
                    executable_path="/Applications/PyCharm.app",
                    window_title="main.py",
                    window_handle="w1",
                    source="macos_active_window",
                ),
                segment_gap_seconds=15,
                idle_seconds=0.0,
            )
        conn.commit()
        conn.close()

        self.server = create_server(self.config, date.today(), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = self.server.url.rstrip("/")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def get(self, path: str):
        return urllib.request.urlopen(self.base_url + path, timeout=5)

    def status_of(self, path: str) -> int:
        try:
            return self.get(path).status
        except urllib.error.HTTPError as exc:
            return exc.code


class RoutingTests(ServerTestCase):
    def test_index_is_served(self) -> None:
        resp = self.get("/")
        self.assertEqual(resp.status, 200)
        body = resp.read().decode("utf-8")
        self.assertIn('id="root"', body)
        self.assertIn("/static/app.js", body)

    def test_report_api_returns_live_derived_data(self) -> None:
        payload = json.loads(self.get("/api/report?date=today").read())
        self.assertEqual(
            sorted(k for k in payload if k != "agent"),
            [
                "activity", "attention", "attention_per_app", "categories",
                "deliverables", "distractions", "meta", "review", "workday",
            ],
        )
        self.assertTrue(payload["meta"]["live"])
        self.assertGreater(payload["attention"]["raw_seconds"], 0)
        self.assertEqual(payload["activity"]["apps"][0]["process"], "PyCharm")

    def test_report_api_accepts_explicit_date(self) -> None:
        payload = json.loads(self.get("/api/report?date=2020-01-01").read())
        self.assertEqual(payload["meta"]["date"], "2020-01-01")
        self.assertFalse(payload["meta"]["live"])
        self.assertEqual(payload["attention"]["segments"], [])

    def test_status_api_reports_agent_state(self) -> None:
        payload = json.loads(self.get("/api/status").read())
        self.assertIn("status", payload)
        self.assertIn("stale", payload)

    def test_static_assets_are_served(self) -> None:
        for path, marker in [
            ("/static/app.js", "FocusLedger"),
            ("/static/app.css", "--accent"),
            ("/static/vendor/react.production.min.js", "createElement"),
        ]:
            resp = self.get(path)
            self.assertEqual(resp.status, 200, path)
            self.assertIn(marker, resp.read().decode("utf-8"), path)

    def test_unknown_route_is_404(self) -> None:
        self.assertEqual(self.status_of("/definitely-not-a-route"), 404)


class SecurityTests(ServerTestCase):
    def test_path_traversal_is_rejected(self) -> None:
        for attempt in [
            "/static/../../../../etc/passwd",
            "/static/..%2f..%2f..%2fetc%2fpasswd",
            "/static/vendor/../../../cli.py",
        ]:
            self.assertEqual(self.status_of(attempt), 404, attempt)

    def test_source_files_are_not_reachable(self) -> None:
        self.assertEqual(self.status_of("/static/../app.py"), 404)

    def test_invalid_date_is_rejected(self) -> None:
        self.assertEqual(self.status_of("/api/report?date=not-a-date"), 400)

    def test_server_refuses_non_loopback_bind(self) -> None:
        for host in ["0.0.0.0", "10.0.0.5", ""]:
            with self.assertRaises(ValueError):
                create_server(self.config, date.today(), host=host, port=0)

    def test_bound_address_is_loopback(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class DaysAndAnalystTests(ServerTestCase):
    def post(self, path: str, body: object, content_type: str = "application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path, data=data, method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def wait_for(self, job_id: str) -> dict:
        deadline = time.time() + 5
        while time.time() < deadline:
            job = json.loads(self.get(f"/api/analyst/jobs/{job_id}").read())
            if job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("analyst job did not finish")

    def test_days_api_lists_tracked_dates_and_the_initial_day(self) -> None:
        payload = json.loads(self.get("/api/days").read())
        today = date.today().isoformat()
        self.assertEqual(payload["dates"], [today])
        self.assertEqual(payload["initial"], today)
        status = json.loads(self.get("/api/status").read())
        self.assertEqual(status["pending_archive_days"], 0)

    def test_storage_api_reports_sizes_and_layers(self) -> None:
        payload = json.loads(self.get("/api/storage").read())
        self.assertEqual(payload["file_bytes"], self.config.database_path.stat().st_size)
        self.assertGreater(payload["used_bytes"], 0)
        self.assertGreaterEqual(payload["free_bytes"], 0)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            {k: payload["raw"][k] for k in ("rows", "days", "retention_days", "pending_archive_days")},
            {"rows": 30, "days": 1, "retention_days": 7, "pending_archive_days": 0},
        )
        self.assertEqual(payload["archived"]["days"], 0)

    def test_api_refuses_a_foreign_host_header(self) -> None:
        host, port = self.server.server_address[:2]
        for method, path in [("GET", "/api/days"), ("POST", "/api/analyst/ask")]:
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(method, path, body=b"{}",
                         headers={"Host": f"rebound.example:{port}",
                                  "Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 403, path)
            conn.close()
        self.assertEqual(self.status_of("/"), 200)  # the page itself stays reachable

    def test_question_runs_as_a_job_and_reports_tool_calls(self) -> None:
        def fake_analyze(conn, config, question, **options):
            options["on_tool_call"](
                analyst_service.ToolCall("list_days", {}, 0.01, {"tracked_days": 1})
            )
            return analyst_service.AnalysisResult(
                question, "fake:9b", "只有今天有数据。", "answered", 2, 0.1, tool_mode="native",
            )

        with mock.patch.object(analyst_service, "analyze", fake_analyze):
            status, job = self.post("/api/analyst/ask", {"question": "有哪些天？"})
            self.assertEqual(status, 202)
            self.assertEqual(job["status"], "running")
            done = self.wait_for(job["id"])

        self.assertEqual(done["status"], "done")
        self.assertEqual(done["answer"], "只有今天有数据。")
        self.assertEqual(done["model"], "fake:9b")
        self.assertEqual(done["tool_calls"][0]["name"], "list_days")
        self.assertEqual(done["tool_calls"][0]["result"], {"tracked_days": 1})

    def test_question_requests_are_validated(self) -> None:
        cases = [
            (b'{"question": "hi"}', "text/plain", 415),
            (b"not json", "application/json", 400),
            (b'{"question": "   "}', "application/json", 400),
            (b'{"question": "hi", "model": "../../etc"}', "application/json", 400),
            (b'{"question": "hi", "think": "yes"}', "application/json", 400),
        ]
        for body, content_type, expected in cases:
            status, _ = self.post("/api/analyst/ask", body, content_type)
            self.assertEqual(status, expected, body)
        with mock.patch.object(llm_service, "installed_models", return_value=("qwen3.5:9b",)):
            status, payload = self.post("/api/analyst/ask", {"question": "hi", "model": "nope:1b"})
        self.assertEqual(status, 400)
        self.assertIn("not installed", payload["error"])
        self.assertEqual(self.status_of("/api/analyst/jobs/nope"), 404)

    def test_second_question_waits_for_the_first(self) -> None:
        release = threading.Event()

        def slow_analyze(conn, config, question, **options):
            release.wait(5)
            return analyst_service.AnalysisResult(question, "fake", "ok", "answered", 1, 0.1)

        with mock.patch.object(analyst_service, "analyze", slow_analyze):
            first_status, first = self.post("/api/analyst/ask", {"question": "one"})
            second_status, second = self.post("/api/analyst/ask", {"question": "two"})
            release.set()
            self.wait_for(first["id"])

        self.assertEqual((first_status, second_status), (202, 409))
        self.assertEqual(second["job"], first["id"])

    def test_proposals_are_applied_only_when_approved_and_can_be_undone(self) -> None:
        today = date.today().isoformat()
        start = datetime.now(timezone.utc).replace(microsecond=0)

        def proposing_analyze(conn, config, question, **options):
            for label in ("sleep", "meal"):
                options["on_proposal"](changes.Proposal(
                    action="mark_absent",
                    arguments={"date": today, "label": label, "app": None, "note": None,
                               "ranges": [[start.isoformat(), (start + timedelta(hours=1)).isoformat()]]},
                    summary=f"Mark an hour as {label}", preview={"marked_hours": 1.0},
                ))
            options["on_thinking"]("Two stretches to mark.")
            return analyst_service.AnalysisResult(question, "fake", "等你批准。", "answered", 2, 0.1)

        with mock.patch.object(analyst_service, "analyze", proposing_analyze):
            _, job = self.post("/api/analyst/ask", {
                "question": "那一小时在睡觉",
                "history": [{"question": "今天怎么样？", "answer": "还行。"}],
            })
            done = self.wait_for(job["id"])
        self.assertEqual(done["thinking"], ["Two stretches to mark."])
        sleep, meal = done["proposals"]
        self.assertEqual((sleep["status"], meal["status"]), ("pending", "pending"))
        self.assertEqual(self.annotation_count(), 0)

        status, applied = self.post(f"/api/analyst/proposals/{sleep['id']}", {"decision": "approve"})
        self.assertEqual((status, applied["status"]), (200, "applied"))
        self.assertEqual(self.annotation_count(), 1)
        status, _ = self.post(f"/api/analyst/proposals/{sleep['id']}", {"decision": "approve"})
        self.assertEqual(status, 409)
        status, dismissed = self.post(f"/api/analyst/proposals/{meal['id']}", {"decision": "dismiss"})
        self.assertEqual((status, dismissed["status"]), (200, "dismissed"))

        listed = json.loads(self.get("/api/changes").read())["changes"]
        self.assertEqual([c["id"] for c in listed], [applied["change_id"]])
        status, undone = self.post(f"/api/changes/{applied['change_id']}/undo", {})
        self.assertEqual(status, 200)
        self.assertIsNotNone(undone["undone_at"])
        self.assertEqual(self.annotation_count(), 0)
        job_after = json.loads(self.get(f"/api/analyst/jobs/{job['id']}").read())
        self.assertEqual([p["status"] for p in job_after["proposals"]], ["undone", "dismissed"])

    def test_history_must_be_a_short_list_of_turns(self) -> None:
        for history in ("not a list", [{"question": "q"}], [{"question": "q", "answer": "a"}] * 9):
            status, _ = self.post("/api/analyst/ask", {"question": "hi", "history": history})
            self.assertEqual(status, 400, history)

    def annotation_count(self) -> int:
        conn = sqlite3.connect(self.config.database_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM time_annotations").fetchone()[0]
        finally:
            conn.close()

    def test_model_list_reports_an_unreachable_endpoint(self) -> None:
        with mock.patch.object(llm_service, "installed_models",
                               return_value=("qwen3.5:9b", "deepseek-r1:8b")):
            payload = json.loads(self.get("/api/analyst/models").read())
        self.assertTrue(payload["available"])
        self.assertEqual(payload["models"], ["qwen3.5:9b", "deepseek-r1:8b"])
        self.assertEqual(payload["default"], self.config.llm_analyst_model)

        with mock.patch.object(llm_service, "installed_models",
                               side_effect=llm_service.LlmUnavailable("cannot reach")):
            payload = json.loads(self.get("/api/analyst/models").read())
        self.assertFalse(payload["available"])
        self.assertIn("cannot reach", payload["detail"])


class LiveDerivationTests(ServerTestCase):
    def test_new_samples_appear_without_restarting_the_server(self) -> None:
        before = json.loads(self.get("/api/report?date=today").read())
        raw_before = before["attention"]["raw_seconds"]

        conn = connect(self.config.database_path)
        base = datetime.now().astimezone().replace(hour=14, minute=0, second=0, microsecond=0)
        for i in range(20):
            record_activity_snapshot(
                conn,
                device_id="dev",
                snapshot=ActiveWindowSnapshot(
                    captured_at=base + timedelta(seconds=i * 5),
                    process_id=101,
                    process_name="Slack",
                    executable_path="/Applications/Slack.app",
                    window_title="team",
                    window_handle="w2",
                    source="macos_active_window",
                ),
                segment_gap_seconds=15,
                idle_seconds=0.0,
            )
        conn.commit()
        conn.close()

        after = json.loads(self.get("/api/report?date=today").read())
        self.assertGreater(after["attention"]["raw_seconds"], raw_before)
        self.assertIn("Slack", [a["process"] for a in after["activity"]["apps"]])


if __name__ == "__main__":
    unittest.main()
