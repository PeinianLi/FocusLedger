from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.services.activity import (
    list_attention_segments,
    list_timeline,
    summarize_attention,
)
from focusledger.services.attention import AttentionPolicy
from focusledger.services.retention import apply_retention, pending_archive_dates


def snapshot(
    at: datetime,
    title: str,
    process_name: str = "Code.exe",
) -> ActiveWindowSnapshot:
    return ActiveWindowSnapshot(
        captured_at=at,
        process_id=123,
        process_name=process_name,
        executable_path=f"C:\\Apps\\{process_name}",
        window_title=title,
        window_handle="1",
        source="test",
    )


class RetentionTests(unittest.TestCase):
    def test_expired_day_is_archived_before_raw_events_are_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            old = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
            recent = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
            self._insert_event(conn, old, "Old", 0)
            self._insert_event(conn, old + timedelta(seconds=5), "Old", 5)
            self._insert_event(conn, recent, "Recent", 0)
            conn.execute(
                """
                INSERT INTO agent_heartbeats(
                    id, device_id, timestamp, pid, status, message
                )
                VALUES ('old-heartbeat', 'device_test', ?, 1, 'running', NULL),
                       ('new-heartbeat', 'device_test', ?, 1, 'running', NULL)
                """,
                (old.isoformat(), recent.isoformat()),
            )
            conn.commit()

            result = apply_retention(
                conn,
                retention_days=7,
                poll_interval_seconds=1,
                attention_policy=AttentionPolicy(),
                now=datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
                local_timezone=timezone.utc,
            )

            self.assertEqual(result.archived_days, 1)
            self.assertEqual(result.raw_events_deleted, 2)
            self.assertEqual(result.heartbeats_deleted, 1)
            remaining_dates = [
                row["local_date"]
                for row in conn.execute(
                    "SELECT local_date FROM raw_activity_events"
                ).fetchall()
            ]
            self.assertEqual(remaining_dates, ["2026-07-18"])

            timeline = list_timeline(conn, old.date())
            self.assertEqual(len(timeline), 1)
            self.assertEqual(timeline[0]["window_title"], "Old")
            self.assertEqual(timeline[0]["duration_seconds"], 5)

            attention = list_attention_segments(conn, old.date())
            self.assertEqual(len(attention), 1)
            self.assertEqual(attention[0]["attention_state"], "active")
            self.assertEqual(attention[0]["duration_seconds"], 5)

            summary = summarize_attention(
                conn,
                old.date(),
                poll_interval_seconds=1,
                attention_policy=AttentionPolicy(),
            )
            self.assertEqual(summary["per_app"][0]["process_name"], "Code.exe")
            self.assertEqual(summary["per_app"][0]["raw_seconds"], 5)
            daily_table = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'daily_app_attention'
                """
            ).fetchone()
            self.assertIsNone(daily_table)
            conn.close()

    def test_retention_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            old = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
            self._insert_event(conn, old, "Old", 0)

            first = self._run(conn)
            second = self._run(conn)

            self.assertEqual(first.archived_days, 1)
            self.assertEqual(second.archived_days, 0)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM activity_segments"
                ).fetchone()["count"],
                1,
            )
            conn.close()

    def test_pending_archive_dates_lists_unarchived_old_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            for day in (1, 2, 20):
                self._insert_event(
                    conn, datetime(2026, 7, day, 9, 0, tzinfo=timezone.utc), "Day", 0
                )
            now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)

            before = pending_archive_dates(conn, 7, now, timezone.utc)
            self._run(conn)
            after = pending_archive_dates(conn, 7, now, timezone.utc)
            conn.close()

            self.assertEqual(
                [d.isoformat() for d in before], ["2026-07-01", "2026-07-02"]
            )
            self.assertEqual(after, [])

    def test_current_seven_local_dates_remain_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            for day in range(18, 25):
                self._insert_event(
                    conn,
                    datetime(2026, 7, day, 9, 0, tzinfo=timezone.utc),
                    f"Day {day}",
                    0,
                )

            result = self._run(conn)

            self.assertEqual(result.archived_days, 0)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM raw_activity_events"
                ).fetchone()["count"],
                7,
            )
            conn.close()

    def test_per_app_attention_is_unchanged_by_archiving(self) -> None:
        # Code.exe sits untouched until input returns in chrome.exe, so the
        # whole quiet stretch is confirmed hard_idle after the fact.
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            policy = AttentionPolicy(
                low_activity_after_seconds=2,
                attention_gap_after_seconds=3,
                interrupted_after_seconds=4,
                hard_idle_after_seconds=5,
            )
            old = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
            for second in range(9):
                self._insert_event(
                    conn, old + timedelta(seconds=second), "Editor", float(second)
                )
            for second in range(9, 13):
                self._insert_event(
                    conn,
                    old + timedelta(seconds=second),
                    "Browser",
                    0.0,
                    process_name="chrome.exe",
                )

            before = summarize_attention(
                conn, old.date(), poll_interval_seconds=1, attention_policy=policy
            )
            per_app = {row["process_name"]: row for row in before["per_app"]}
            self.assertEqual(before["effective_seconds"], 3)
            self.assertEqual(
                sum(row["effective_seconds"] for row in before["per_app"]), 3
            )
            self.assertEqual(per_app["Code.exe"]["hard_idle"], 8)
            self.assertEqual(per_app["Code.exe"]["effective_seconds"], 0)

            apply_retention(
                conn,
                retention_days=7,
                poll_interval_seconds=1,
                attention_policy=policy,
                now=datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
                local_timezone=timezone.utc,
            )
            after = summarize_attention(
                conn, old.date(), poll_interval_seconds=1, attention_policy=policy
            )

            self.assertEqual(after["per_app"], before["per_app"])
            conn.close()

    def test_idle_stretch_before_midnight_is_not_counted_twice(self) -> None:
        # Input stops at 16:30 and returns at 08:00 the next morning. The next
        # day's first sample backdates that idle to 16:30, but only the part
        # after its own midnight belongs to it.
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            evening = datetime(2026, 7, 16, 16, 30, tzinfo=timezone.utc)
            midnight = datetime(2026, 7, 17, tzinfo=timezone.utc)
            self._insert_event(conn, evening, "Editor", 0.0)
            self._insert_event(
                conn, midnight - timedelta(seconds=1), "Editor", 7.5 * 3600 - 1
            )
            self._insert_event(
                conn, midnight + timedelta(seconds=1), "Editor", 7.5 * 3600 + 1
            )
            self._insert_event(conn, midnight + timedelta(hours=8), "Editor", 0.0)

            def check(stage: str) -> None:
                previous = list_attention_segments(conn, evening.date())
                today = list_attention_segments(conn, midnight.date())
                summary = summarize_attention(
                    conn, midnight.date(), poll_interval_seconds=1
                )
                with self.subTest(stage):
                    self.assertEqual(today[0]["started_at"], midnight.isoformat())
                    self.assertEqual(today[0]["attention_state"], "hard_idle")
                    self.assertEqual(summary["hard_idle_seconds"], 8 * 3600)
                    self.assertLessEqual(
                        previous[-1]["ended_at"], today[0]["started_at"]
                    )

            check("raw")
            self._run(conn)
            check("archived")
            conn.close()

    def test_attention_archived_before_clipping_is_clipped_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO attention_segments(
                    id, local_date, device_id, started_at, ended_at,
                    started_at_utc, ended_at_utc, local_day_start_utc,
                    duration_seconds, attention_state, attention_weight,
                    max_idle_seconds, source, created_at, updated_at
                )
                VALUES (
                    'legacy', '2026-07-17', 'device_test',
                    '2026-07-16T12:30:00-04:00', '2026-07-17T04:00:00-04:00',
                    '2026-07-16T16:30:00+00:00', '2026-07-17T08:00:00+00:00',
                    '2026-07-17T00:00:00+00:00', 55800, 'hard_idle', 0, 55800,
                    'retention_archive', 'now', 'now'
                )
                """
            )
            conn.commit()

            rows = list_attention_segments(
                conn, datetime(2026, 7, 17, tzinfo=timezone.utc).date()
            )
            conn.close()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["started_at"], "2026-07-16T20:00:00-04:00")
            self.assertEqual(rows[0]["started_at_utc"], "2026-07-17T00:00:00+00:00")
            self.assertEqual(rows[0]["duration_seconds"], 8 * 3600)

    def test_attention_without_stored_day_start_is_clipped_at_local_midnight(self) -> None:
        # Days archived before v7 have no local_day_start_utc; the segment's own
        # UTC offset still places local midnight.
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO attention_segments(
                    id, local_date, device_id, started_at, ended_at,
                    started_at_utc, ended_at_utc, duration_seconds,
                    attention_state, attention_weight, max_idle_seconds, source,
                    created_at, updated_at
                )
                VALUES (
                    'legacy', '2026-07-21', 'device_test',
                    '2026-07-20T23:20:00-04:00', '2026-07-21T05:00:00-04:00',
                    '2026-07-21T03:20:00+00:00', '2026-07-21T09:00:00+00:00',
                    20400, 'hard_idle', 0, 20400, 'retention_archive', 'now', 'now'
                )
                """
            )
            conn.commit()

            rows = list_attention_segments(
                conn, datetime(2026, 7, 21, tzinfo=timezone.utc).date()
            )
            conn.close()

            self.assertEqual(rows[0]["started_at"], "2026-07-21T00:00:00-04:00")
            self.assertEqual(rows[0]["duration_seconds"], 5 * 3600)

    @staticmethod
    def _insert_event(
        conn,
        at: datetime,
        title: str,
        idle_seconds: float,
        process_name: str = "Code.exe",
    ) -> None:
        from focusledger.services.activity import record_activity_snapshot

        record_activity_snapshot(
            conn,
            device_id="device_test",
            snapshot=snapshot(at, title, process_name),
            segment_gap_seconds=15,
            idle_seconds=idle_seconds,
        )

    @staticmethod
    def _run(conn):
        return apply_retention(
            conn,
            retention_days=7,
            poll_interval_seconds=1,
            attention_policy=AttentionPolicy(),
            now=datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
            local_timezone=timezone.utc,
        )


if __name__ == "__main__":
    unittest.main()
