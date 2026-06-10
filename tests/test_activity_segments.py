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
from focusledger.services.privacy import PRIVATE_BROWSER_TITLE, redact_existing_private_browser_titles


def make_snapshot(
    captured_at: datetime,
    process_name: str,
    title: str,
    window_handle: str = "1",
) -> ActiveWindowSnapshot:
    return ActiveWindowSnapshot(
        captured_at=captured_at,
        process_id=123,
        process_name=process_name,
        executable_path=f"C:\\Apps\\{process_name}",
        window_title=title,
        window_handle=window_handle,
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
                snapshot=make_snapshot(base, "Code.exe", "Docs"),
                segment_gap_seconds=15,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base + timedelta(seconds=5), "Code.exe", "Tests"),
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

    def test_normal_browser_titles_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base, "chrome.exe", "Sensitive page title - Google Chrome"),
                segment_gap_seconds=15,
            )

            raw_title = conn.execute("SELECT window_title FROM raw_activity_events").fetchone()["window_title"]
            segment_title = conn.execute("SELECT window_title FROM activity_segments").fetchone()["window_title"]
            self.assertEqual(raw_title, "Sensitive page title - Google Chrome")
            self.assertEqual(segment_title, "Sensitive page title - Google Chrome")
            conn.close()

    def test_private_browser_window_titles_are_redacted_and_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base, "chrome.exe", "New Incognito Tab - Google Chrome", "private1"),
                segment_gap_seconds=15,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base + timedelta(seconds=5), "chrome.exe", "Sensitive page - Google Chrome", "private1"),
                segment_gap_seconds=15,
            )

            raw_titles = [
                row["window_title"]
                for row in conn.execute("SELECT window_title FROM raw_activity_events ORDER BY captured_at").fetchall()
            ]
            segments = conn.execute("SELECT window_title, duration_seconds FROM activity_segments").fetchall()
            self.assertEqual(raw_titles, [PRIVATE_BROWSER_TITLE, PRIVATE_BROWSER_TITLE])
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["window_title"], PRIVATE_BROWSER_TITLE)
            self.assertEqual(segments[0]["duration_seconds"], 5)
            conn.close()

    def test_existing_private_browser_titles_can_be_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)
            later = base + timedelta(seconds=5)
            conn.execute(
                """
                INSERT INTO raw_activity_events(
                    id, device_id, captured_at, process_id, process_name, executable_path,
                    window_title, window_handle, source, created_at
                )
                VALUES ('raw1', 'device_test', ?, 1, 'chrome.exe', 'chrome.exe', 'New Incognito Tab', 'private1', 'test', ?)
                """,
                (
                    base.isoformat(),
                    base.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO activity_segments(
                    id, device_id, started_at, ended_at, duration_seconds, process_name,
                    window_title, executable_path, source, created_at, updated_at
                )
                VALUES ('seg1', 'device_test', ?, ?, 0, 'chrome.exe', 'Sensitive', 'chrome.exe', 'test', ?, ?)
                """,
                (
                    base.isoformat(),
                    later.isoformat(),
                    base.isoformat(),
                    base.isoformat(),
                ),
            )
            conn.commit()

            counts = redact_existing_private_browser_titles(conn)
            self.assertEqual(counts["raw_activity_events"], 1)
            self.assertEqual(counts["activity_segments"], 1)
            raw_title = conn.execute("SELECT window_title FROM raw_activity_events").fetchone()["window_title"]
            segment_title = conn.execute("SELECT window_title FROM activity_segments").fetchone()["window_title"]
            self.assertEqual(raw_title, PRIVATE_BROWSER_TITLE)
            self.assertEqual(segment_title, PRIVATE_BROWSER_TITLE)
            conn.close()


if __name__ == "__main__":
    unittest.main()
