from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from focusledger.config import build_default_config, save_config
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.server.app import create_server
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
