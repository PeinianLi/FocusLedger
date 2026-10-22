from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.services.activity import (
    list_attention_segments,
    list_timeline,
    record_activity_snapshot,
    summarize_attention,
    summarize_by_app,
)
from focusledger.services.attention import AttentionPolicy
from focusledger.services.privacy import PRIVATE_BROWSER_TITLE, redact_existing_private_browser_titles


def make_snapshot(
    captured_at: datetime,
    process_name: str | None,
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
    def test_dst_fallback_uses_utc_order_without_splitting_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            eastern = ZoneInfo("America/New_York")
            before_fallback = datetime(
                2026, 11, 1, 1, 59, 59, tzinfo=eastern, fold=0
            )
            after_fallback = datetime(
                2026, 11, 1, 1, 0, 0, tzinfo=eastern, fold=1
            )

            for captured_at in (before_fallback, after_fallback):
                record_activity_snapshot(
                    conn,
                    device_id="device_test",
                    snapshot=make_snapshot(
                        captured_at, "Code.exe", "DST-safe timeline"
                    ),
                    segment_gap_seconds=15,
                )

            rows = list_timeline(conn, before_fallback.date())
            conn.close()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["duration_seconds"], 1)
            self.assertLess(rows[0]["started_at_utc"], rows[0]["ended_at_utc"])

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

            rows = list_timeline(conn, base.date())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["process_name"], "Code.exe")
            self.assertEqual(rows[0]["duration_seconds"], 5)
            stored_segments = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'activity_segments'
                """
            ).fetchone()
            self.assertIsNotNone(stored_segments)
            stored_count = conn.execute("SELECT COUNT(*) FROM activity_segments").fetchone()[0]
            self.assertEqual(stored_count, 0)
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

            rows = list_timeline(conn, base.date())
            self.assertEqual(len(rows), 2)
            conn.close()

    def test_unknown_process_samples_merge_into_one_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 21, 35, tzinfo=timezone.utc)

            for offset in range(20):
                record_activity_snapshot(
                    conn,
                    device_id="device_test",
                    snapshot=make_snapshot(
                        base + timedelta(seconds=offset),
                        None,
                        "",
                    ),
                    segment_gap_seconds=15,
                )

            rows = list_timeline(conn, base.date())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["process_name"], "(unknown)")
            self.assertEqual(rows[0]["duration_seconds"], 19)
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
            segment_title = list_timeline(conn, base.date())[0]["window_title"]
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
                for row in conn.execute("SELECT window_title FROM raw_activity_events ORDER BY captured_at_utc").fetchall()
            ]
            segments = list_timeline(conn, base.date())
            self.assertEqual(raw_titles, [PRIVATE_BROWSER_TITLE, PRIVATE_BROWSER_TITLE])
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["window_title"], PRIVATE_BROWSER_TITLE)
            self.assertEqual(segments[0]["duration_seconds"], 5)
            conn.close()

    def test_chinese_incognito_title_is_redacted_and_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(
                    base,
                    "chrome.exe",
                    "\u65b0\u7684\u65e0\u75d5\u5f0f\u6807\u7b7e\u9875 - Google Chrome",
                    "private_cn",
                ),
                segment_gap_seconds=15,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(
                    base + timedelta(seconds=5),
                    "chrome.exe",
                    "Sensitive page - Google Chrome",
                    "private_cn",
                ),
                segment_gap_seconds=15,
            )

            rows = list_timeline(conn, base.date())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["window_title"], PRIVATE_BROWSER_TITLE)
            self.assertEqual(rows[0]["duration_seconds"], 5)
            conn.close()

    def test_attention_summary_is_derived_from_raw_idle_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime.now().astimezone().replace(
                hour=9,
                minute=0,
                second=0,
                microsecond=0,
            )
            policy = AttentionPolicy(
                low_activity_after_seconds=2,
                attention_gap_after_seconds=3,
                interrupted_after_seconds=4,
                hard_idle_after_seconds=5,
            )
            samples = (
                (0, 0.0),
                (4, 4.0),
                (10, None),
            )

            for offset, idle_seconds in samples:
                record_activity_snapshot(
                    conn,
                    device_id="device_test",
                    snapshot=make_snapshot(
                        base + timedelta(seconds=offset),
                        "Code.exe",
                        "FocusLedger",
                    ),
                    segment_gap_seconds=15,
                    idle_seconds=idle_seconds,
                )

            result = summarize_attention(
                conn,
                base.date(),
                poll_interval_seconds=1,
                attention_policy=policy,
            )
            self.assertEqual(result["active_seconds"], 2)
            self.assertEqual(result["low_activity_seconds"], 1)
            self.assertEqual(result["attention_gap_seconds"], 1)
            self.assertEqual(result["unknown_seconds"], 0)
            self.assertEqual(result["effective_seconds"], 2.5)
            self.assertEqual(result["per_app"][0]["process_name"], "Code.exe")
            conn.close()

    def test_attention_segments_are_backfilled_from_last_input_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(
                    base + timedelta(seconds=1800),
                    "Code.exe",
                    "FocusLedger",
                ),
                segment_gap_seconds=15,
                idle_seconds=1800,
            )

            rows = list_attention_segments(conn, base.date())
            self.assertEqual(
                [row["attention_state"] for row in rows],
                [
                    "active",
                    "low_activity",
                    "attention_gap",
                    "interrupted",
                    "hard_idle",
                ],
            )
            self.assertEqual(
                [row["duration_seconds"] for row in rows],
                [180, 420, 600, 600, 0],
            )
            self.assertEqual(rows[0]["started_at"], base.isoformat())
            self.assertEqual(
                rows[0]["ended_at"],
                (base + timedelta(seconds=180)).isoformat(),
            )
            self.assertEqual(
                [row["attention_weight"] for row in rows],
                [1.0, 0.5, 0.0, 0.0, 0.0],
            )
            conn.close()

    def test_attention_segments_continue_from_previous_end_without_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 13, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(
                    base + timedelta(minutes=1),
                    "Code.exe",
                    "FocusLedger",
                ),
                segment_gap_seconds=15,
                idle_seconds=60,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(
                    base + timedelta(minutes=4),
                    "Code.exe",
                    "FocusLedger",
                ),
                segment_gap_seconds=15,
                idle_seconds=240,
            )

            rows = list_attention_segments(conn, base.date())
            self.assertEqual(
                [row["attention_state"] for row in rows],
                ["active", "low_activity"],
            )
            self.assertEqual(rows[0]["started_at"], base.isoformat())
            self.assertEqual(
                rows[0]["ended_at"],
                (base + timedelta(minutes=3)).isoformat(),
            )
            self.assertEqual(
                rows[1]["started_at"],
                (base + timedelta(minutes=3)).isoformat(),
            )
            self.assertEqual(
                rows[1]["ended_at"],
                (base + timedelta(minutes=4)).isoformat(),
            )
            self.assertEqual(
                [row["duration_seconds"] for row in rows],
                [180, 60],
            )
            conn.close()

    def test_next_input_rewrites_long_idle_gap_as_hard_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 13, 0, tzinfo=timezone.utc)

            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(base, "Code.exe", "FocusLedger"),
                segment_gap_seconds=15,
                idle_seconds=0,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(
                    base + timedelta(minutes=30),
                    "Code.exe",
                    "FocusLedger",
                ),
                segment_gap_seconds=15,
                idle_seconds=1800,
            )
            record_activity_snapshot(
                conn,
                device_id="device_test",
                snapshot=make_snapshot(
                    base + timedelta(hours=1),
                    "Code.exe",
                    "FocusLedger",
                ),
                segment_gap_seconds=15,
                idle_seconds=0,
            )

            rows = list_attention_segments(conn, base.date())
            self.assertEqual(
                [(row["attention_state"], row["duration_seconds"]) for row in rows],
                [
                    ("hard_idle", 3600),
                    ("active", 0),
                ],
            )
            self.assertEqual(rows[0]["started_at"], base.isoformat())
            self.assertEqual(
                rows[0]["ended_at"],
                (base + timedelta(hours=1)).isoformat(),
            )
            conn.close()

    def test_existing_private_browser_titles_can_be_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "focusledger.sqlite3")
            base = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)
            later = base + timedelta(seconds=5)
            application_id = conn.execute(
                """
                INSERT INTO applications(process_name, executable_path, first_seen_at)
                VALUES ('chrome.exe', 'chrome.exe', ?)
                """,
                (base.isoformat(),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO raw_activity_events(
                    device_id, captured_at_utc, local_date, process_id, application_id,
                    window_title, window_handle, source
                )
                VALUES ('device_test', ?, ?, 1, ?, 'New Incognito Tab', 'private1', 'test')
                """,
                (
                    base.isoformat(),
                    base.date().isoformat(),
                    application_id,
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
