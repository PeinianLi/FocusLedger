from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.services.activity import record_activity_snapshot, summarize_by_app


def make_snapshot(captured_at: datetime, process_name: str, title: str) -> ActiveWindowSnapshot:
    return ActiveWindowSnapshot(
        captured_at=captured_at,
        process_id=123,
        process_name=process_name,
        executable_path=f"C:\\Apps\\{process_name}",
        window_title=title,
        window_handle="1",
    )


def open_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    apply_migrations(conn)
    return conn


class ActivitySegmentTests(unittest.TestCase):
    def test_same_window_samples_merge_into_one_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base, "Code.exe", "FocusLedger"),
                segment_gap_seconds=15,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base + timedelta(seconds=5), "Code.exe", "FocusLedger"),
                segment_gap_seconds=15,
            )

            rows = conn.execute("SELECT duration_seconds, process_name FROM activity_segments").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["process_name"], "Code.exe")
            self.assertEqual(rows[0]["duration_seconds"], 5)
            conn.close()

    def test_changed_title_creates_new_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base, "chrome.exe", "Docs"),
                segment_gap_seconds=15,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base + timedelta(seconds=5), "chrome.exe", "YouTube"),
                segment_gap_seconds=15,
            )

            count = conn.execute("SELECT COUNT(*) AS count FROM activity_segments").fetchone()["count"]
            self.assertEqual(count, 2)
            conn.close()

    def test_summary_groups_by_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base, "Code.exe", "FocusLedger"),
                segment_gap_seconds=15,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base + timedelta(seconds=10), "Code.exe", "FocusLedger"),
                segment_gap_seconds=15,
            )

            rows = summarize_by_app(conn, base.date())
            self.assertEqual(rows[0]["process_name"], "Code.exe")
            self.assertEqual(rows[0]["total_seconds"], 10)
            conn.close()


if __name__ == "__main__":
    unittest.main()
