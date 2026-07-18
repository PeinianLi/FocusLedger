from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.services.activity import record_activity_snapshot
from focusledger.services.report import (
    DATA_TOKEN,
    build_report_data,
    render_report_html,
)


def open_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    apply_migrations(conn)
    return conn


def snap(captured_at: datetime, process: str | None, title: str) -> ActiveWindowSnapshot:
    return ActiveWindowSnapshot(
        captured_at=captured_at,
        process_id=100,
        process_name=process,
        executable_path=(f"/Applications/{process}.app" if process else None),
        window_title=title,
        window_handle="w1",
        source="macos_active_window",
    )


def embedded_json(html: str) -> dict:
    match = re.search(r'application/json">(.*?)</script>', html, re.S)
    assert match is not None
    return json.loads(match.group(1))


class BuildReportDataTests(unittest.TestCase):
    def populate(self, conn, base: datetime) -> None:
        # First half active (idle 0), second half idle ramps up.
        for i in range(40):
            captured = base + timedelta(seconds=i * 5)
            idle = 0.0 if i < 20 else (i - 20) * 30.0
            record_activity_snapshot(
                conn,
                device_id="dev",
                snapshot=snap(captured, "PyCharm", "main.py"),
                segment_gap_seconds=15,
                idle_seconds=idle,
            )

    def test_report_has_expected_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "db.sqlite3")
            base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
            self.populate(conn, base)

            data = build_report_data(conn, base.date(), poll_interval_seconds=5)

            self.assertEqual(
                sorted(data.keys()),
                ["activity", "attention", "attention_per_app", "meta"],
            )
            att = data["attention"]
            self.assertIn("totals", att)
            self.assertIn("segments", att)
            self.assertGreater(att["raw_seconds"], 0)
            for seg in att["segments"]:
                self.assertIn("start_min", seg)
                self.assertIn("state", seg)
                self.assertGreaterEqual(seg["start_min"], 0)
                self.assertLess(seg["start_min"], 24 * 60)
            self.assertTrue(data["activity"]["apps"])
            self.assertEqual(data["activity"]["apps"][0]["process"], "PyCharm")
            conn.close()

    def test_minutes_since_midnight_uses_wall_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "db.sqlite3")
            base = datetime(2026, 7, 16, 9, 30, 0, tzinfo=timezone.utc)
            record_activity_snapshot(
                conn,
                device_id="dev",
                snapshot=snap(base, "PyCharm", "main.py"),
                segment_gap_seconds=15,
                idle_seconds=0.0,
            )
            data = build_report_data(conn, base.date(), poll_interval_seconds=5)
            seg = data["activity"]["segments"][0]
            self.assertAlmostEqual(seg["start_min"], 9 * 60 + 30, places=3)
            self.assertEqual(seg["start_label"], "09:30:00")
            conn.close()

    def test_empty_day_produces_valid_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "db.sqlite3")
            data = build_report_data(conn, datetime(2026, 1, 1).date(), poll_interval_seconds=5)
            self.assertEqual(data["attention"]["segments"], [])
            self.assertEqual(data["activity"]["segments"], [])
            self.assertEqual(data["activity"]["apps"], [])
            # Still renders without raising.
            html = render_report_html(data)
            self.assertIn("FOCUS", html)
            conn.close()


class RenderReportHtmlTests(unittest.TestCase):
    def base_data(self) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "db.sqlite3")
            base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
            record_activity_snapshot(
                conn,
                device_id="dev",
                snapshot=snap(base, "PyCharm", "main.py"),
                segment_gap_seconds=15,
                idle_seconds=0.0,
            )
            data = build_report_data(conn, base.date(), poll_interval_seconds=5)
            conn.close()
            return data

    def test_token_is_replaced_and_json_round_trips(self) -> None:
        data = self.base_data()
        html = render_report_html(data)
        self.assertNotIn(DATA_TOKEN, html)
        parsed = embedded_json(html)
        self.assertEqual(parsed["meta"]["date"], data["meta"]["date"])

    def test_angle_bracket_in_title_cannot_break_out_of_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "db.sqlite3")
            base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
            record_activity_snapshot(
                conn,
                device_id="dev",
                snapshot=snap(base, "PyCharm", "</script><script>alert(1)</script>"),
                segment_gap_seconds=15,
                idle_seconds=0.0,
            )
            data = build_report_data(conn, base.date(), poll_interval_seconds=5)
            conn.close()

        html = render_report_html(data)
        # The injected data must not contain a literal closing script tag.
        payload = re.search(r'application/json">(.*?)</script>', html, re.S).group(1)
        self.assertNotIn("<", payload)
        # And the title survives once JSON-decoded.
        parsed = json.loads(payload)
        titles = [s["title"] for s in parsed["activity"]["segments"]]
        self.assertIn("</script><script>alert(1)</script>", titles)


if __name__ == "__main__":
    unittest.main()
