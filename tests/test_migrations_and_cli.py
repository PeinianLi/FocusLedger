from __future__ import annotations

import sqlite3
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from focusledger.agent.heartbeat import (
    HEARTBEAT_INTERVAL_SECONDS,
    HeartbeatThrottle,
    heartbeat_stale_after,
)
from focusledger.agent.runner import _system_local_date
from focusledger.cli import build_parser, heartbeat_age_seconds, render_attention_timeline
from focusledger.config import build_default_config, load_config
from focusledger.db.connection import connect
from focusledger.db.migrations import SCHEMA_VERSION, SchemaTooNewError, apply_migrations
from focusledger.services.activity import _list_raw_activity_events


class MigrationAndCliTests(unittest.TestCase):
    def test_v1_database_adds_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "focusledger.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE raw_activity_events (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    process_id INTEGER,
                    process_name TEXT,
                    executable_path TEXT,
                    window_title TEXT,
                    window_handle TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO raw_activity_events VALUES (
                    'v1', 'device_test', '2026-07-01T09:00:00-04:00', 7,
                    'Code.exe', 'C:\\Apps\\Code.exe', 'Editor', 'h1',
                    'windows_active_window', '2026-07-01T13:00:00+00:00'
                )
                """
            )
            conn.commit()
            conn.close()

            conn = connect(db_path)
            apply_migrations(conn)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(raw_activity_events)").fetchall()
            }
            version = conn.execute(
                "SELECT value FROM app_metadata WHERE key = 'schema_version'"
            ).fetchone()["value"]
            attention_table = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'attention_segments'
                """
            ).fetchone()
            samples = _list_raw_activity_events(conn, date(2026, 7, 1))
            conn.close()

            self.assertIn("idle_seconds", columns)
            self.assertIn("captured_at_utc", columns)
            self.assertIn("local_date", columns)
            self.assertIn("time_context_id", columns)
            self.assertIn("application_id", columns)
            self.assertNotIn("captured_at", columns)
            self.assertNotIn("process_name", columns)
            self.assertNotIn("attention_state", columns)
            self.assertNotIn("attention_weight", columns)
            self.assertIsNotNone(attention_table)
            self.assertEqual(version, str(SCHEMA_VERSION))
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["captured_at"], "2026-07-01T09:00:00-04:00")
            self.assertEqual(samples[0]["process_name"], "Code.exe")
            self.assertEqual(samples[0]["window_title"], "Editor")

    def test_v8_raw_rows_survive_v9_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "focusledger.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE raw_activity_events (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    captured_at_utc TEXT,
                    local_date TEXT,
                    timezone_id TEXT,
                    timezone_name TEXT,
                    utc_offset_seconds INTEGER,
                    is_dst INTEGER,
                    local_day_start_utc TEXT,
                    process_id INTEGER,
                    process_name TEXT,
                    executable_path TEXT,
                    window_title TEXT,
                    window_handle TEXT,
                    source TEXT NOT NULL,
                    idle_seconds REAL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX idx_raw_activity_events_captured_at
                ON raw_activity_events(captured_at);
                CREATE INDEX idx_raw_activity_events_local_date_utc
                ON raw_activity_events(local_date, captured_at_utc);
                CREATE TABLE applications (
                    id TEXT PRIMARY KEY,
                    process_name TEXT NOT NULL,
                    executable_path TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(process_name, executable_path)
                );
                INSERT INTO applications VALUES (
                    'app1', 'Code.exe', 'C:\\Apps\\Code.exe',
                    '2026-07-01T00:00:00+00:00', '2026-07-24T13:00:01+00:00'
                );
                """
            )
            rows = [
                ("2026-07-24T09:00:00.250000-04:00", "Code.exe", "C:\\Apps\\Code.exe", "Editor", 0.5),
                ("2026-07-24T09:00:01-04:00", "Code.exe", "C:\\Apps\\Code.exe", "Editor", 1.5),
                ("2026-07-24T09:00:02-04:00", None, None, None, None),
            ]
            for index, (local, process, exe, title, idle) in enumerate(rows):
                utc = datetime.fromisoformat(local).astimezone(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO raw_activity_events VALUES (
                        ?, 'device_test', ?, ?, '2026-07-24', 'Eastern Standard Time',
                        'Eastern Daylight Time', -14400, 1, '2026-07-24T04:00:00+00:00',
                        42, ?, ?, ?, 'h1', 'windows_active_window', ?, ?
                    )
                    """,
                    (f"raw{index}", local, utc, process, exe, title, idle, utc),
                )
            conn.commit()
            conn.close()

            conn = connect(db_path)
            apply_migrations(conn)
            samples = _list_raw_activity_events(conn, date(2026, 7, 24))
            applications = [
                tuple(row)
                for row in conn.execute(
                    "SELECT process_name, executable_path, first_seen_at FROM applications"
                ).fetchall()
            ]
            contexts = conn.execute(
                "SELECT COUNT(*) AS count FROM time_contexts"
            ).fetchone()["count"]
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            leftover = conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%_v9'"
            ).fetchall()
            conn.close()

            self.assertEqual([s["captured_at"] for s in samples], [r[0] for r in rows])
            self.assertEqual(
                [
                    (s["process_name"], s["executable_path"], s["window_title"], s["idle_seconds"])
                    for s in samples
                ],
                [r[1:] for r in rows],
            )
            self.assertEqual({s["timezone_name"] for s in samples}, {"Eastern Daylight Time"})
            self.assertEqual({s["utc_offset_seconds"] for s in samples}, {-14400})
            self.assertEqual(
                applications,
                [("Code.exe", "C:\\Apps\\Code.exe", "2026-07-01T00:00:00+00:00")],
            )
            self.assertEqual(contexts, 1)
            self.assertIn("idx_raw_activity_events_local_date_utc", indexes)
            self.assertNotIn("idx_raw_activity_events_captured_at", indexes)
            self.assertEqual(leftover, [])

            # A second run finds nothing left to rebuild.
            conn = connect(db_path)
            apply_migrations(conn)
            self.assertEqual(len(_list_raw_activity_events(conn, date(2026, 7, 24))), 3)
            conn.close()

    def test_newer_database_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            with self.assertRaises(SchemaTooNewError):
                apply_migrations(conn)
            conn.close()

    def test_v9_database_gains_foreign_keys_and_a_global_workday_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "focusledger.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE workday_sessions (
                    id TEXT PRIMARY KEY, device_id TEXT NOT NULL, workday_key TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT, timezone TEXT, status TEXT NOT NULL,
                    is_overtime INTEGER NOT NULL DEFAULT 0, offwork_target_time TEXT,
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX idx_workday_sessions_key
                    ON workday_sessions(device_id, workday_key);
                CREATE TABLE workday_pauses (
                    id TEXT PRIMARY KEY, workday_id TEXT NOT NULL, started_at TEXT NOT NULL,
                    ended_at TEXT, reason TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE deliverables (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', priority INTEGER NOT NULL DEFAULT 2,
                    due_date TEXT, match_keywords TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, deliverable_id TEXT, title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open', estimate_minutes INTEGER,
                    blocker TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO workday_sessions(id, device_id, workday_key, started_at, status,
                                             created_at, updated_at)
                VALUES ('s1', 'device_a', '2026-07-24', 'now', 'ended', 'now', 'now');
                INSERT INTO workday_pauses VALUES ('p1', 's1', 'now', NULL, 'lunch', 'now');
                INSERT INTO deliverables(id, title, created_at, updated_at)
                VALUES ('d1', 'Report', 'now', 'now');
                INSERT INTO tasks(id, deliverable_id, title, created_at, updated_at)
                VALUES ('t1', 'd1', 'Draft', 'now', 'now'),
                       ('t2', 'gone', 'Orphan', 'now', 'now');
                """
            )
            conn.commit()
            conn.close()

            conn = connect(db_path)
            apply_migrations(conn)
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            task_keys = conn.execute("PRAGMA foreign_key_list(tasks)").fetchall()
            pause_keys = conn.execute("PRAGMA foreign_key_list(workday_pauses)").fetchall()
            tasks = dict(
                conn.execute("SELECT id, deliverable_id FROM tasks").fetchall()
            )
            pauses = conn.execute("SELECT id FROM workday_pauses").fetchall()
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO workday_sessions(id, device_id, workday_key, started_at,
                                                 status, created_at, updated_at)
                    VALUES ('s2', 'device_b', '2026-07-24', 'now', 'active', 'now', 'now')
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO tasks(id, deliverable_id, title, created_at, updated_at) "
                    "VALUES ('t3', 'missing', 'x', 'now', 'now')"
                )
            conn.rollback()

            # Already current: a second run changes nothing.
            apply_migrations(conn)
            conn.close()

            self.assertEqual(user_version, SCHEMA_VERSION)
            self.assertEqual(task_keys[0]["table"], "deliverables")
            self.assertEqual(pause_keys[0]["table"], "workday_sessions")
            self.assertEqual(tasks, {"t1": "d1", "t2": None})
            self.assertEqual(len(pauses), 1)
            self.assertNotIn("idx_workday_sessions_key", indexes)
            self.assertIn("idx_workday_sessions_workday_key", indexes)
            self.assertIn("idx_tasks_deliverable", indexes)
            self.assertIn("idx_classification_rules_target", indexes)

    def test_v5_phase_tables_and_v7_cooked_tables_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            conn.close()

            self.assertIn("activity_segments", tables)
            self.assertIn("attention_segments", tables)
            self.assertIn("workday_sessions", tables)
            self.assertIn("classification_rules", tables)
            self.assertIn("deliverables", tables)
            self.assertIn("daily_reviews", tables)

    def test_v6_daily_attention_table_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "focusledger.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE daily_app_attention (
                    local_date TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    process_name TEXT NOT NULL,
                    raw_seconds REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO daily_app_attention
                VALUES ('2026-07-01', 'device', 'Code.exe', 60)
                """
            )
            conn.commit()
            conn.close()

            conn = connect(db_path)
            apply_migrations(conn)
            table = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'daily_app_attention'
                """
            ).fetchone()
            version = conn.execute(
                "SELECT value FROM app_metadata WHERE key = 'schema_version'"
            ).fetchone()["value"]
            conn.close()

            self.assertIsNone(table)
            self.assertEqual(version, str(SCHEMA_VERSION))

    def test_legacy_derived_tables_receive_local_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "focusledger.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE activity_segments (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    process_name TEXT,
                    window_title TEXT,
                    executable_path TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE attention_segments (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    attention_state TEXT NOT NULL,
                    attention_weight REAL NOT NULL,
                    max_idle_seconds REAL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO activity_segments
                VALUES (
                    'activity', 'device', '2026-07-01T09:00:00+00:00',
                    '2026-07-01T09:01:00+00:00', 60, 'Code.exe', 'Title',
                    'Code.exe', 'legacy', 'now', 'now'
                );
                INSERT INTO attention_segments
                VALUES (
                    'attention', 'device', '2026-07-01T09:00:00+00:00',
                    '2026-07-01T09:01:00+00:00', 60, 'active', 1, 0,
                    'legacy', 'now', 'now'
                );
                """
            )
            conn.commit()
            conn.close()

            conn = connect(db_path)
            apply_migrations(conn)
            activity_date = conn.execute(
                "SELECT local_date FROM activity_segments"
            ).fetchone()["local_date"]
            attention_date = conn.execute(
                "SELECT local_date FROM attention_segments"
            ).fetchone()["local_date"]
            conn.close()

            self.assertEqual(activity_date, "2026-07-01")
            self.assertEqual(attention_date, "2026-07-01")

    def test_start_parser_accepts_background(self) -> None:
        args = build_parser().parse_args(["start", "--background"])
        self.assertTrue(args.background)
        self.assertFalse(args.background_child)

    def test_old_running_heartbeat_is_stale_by_age(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.assertGreater(heartbeat_age_seconds(old.isoformat()), 299)

    def test_heartbeat_throttle_writes_on_status_change_or_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "focusledger.sqlite3")
            apply_migrations(conn)
            now = [0.0]
            throttle = HeartbeatThrottle(clock=lambda: now[0])

            written = []
            for moment, status in (
                (0, "running"),
                (1, "running"),
                (2, "error"),
                (3, "error"),
                (4, "running"),
                (HEARTBEAT_INTERVAL_SECONDS + 3, "running"),
                (HEARTBEAT_INTERVAL_SECONDS + 4, "running"),
            ):
                now[0] = float(moment)
                if throttle.beat(conn, device_id="device_test", status=status):
                    written.append((moment, status))
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM agent_heartbeats"
            ).fetchone()["count"]
            conn.close()

            self.assertEqual(
                written,
                [
                    (0, "running"),
                    (2, "error"),
                    (4, "running"),
                    (HEARTBEAT_INTERVAL_SECONDS + 4, "running"),
                ],
            )
            self.assertEqual(count, 4)
            # Missing-data detection and staleness must tolerate a full interval.
            self.assertGreater(heartbeat_stale_after(1), HEARTBEAT_INTERVAL_SECONDS)

    def test_old_config_receives_attention_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            raw = build_default_config(data_dir).to_json_dict()
            for key in (
                "low_activity_after_seconds",
                "attention_gap_after_seconds",
                "interrupted_after_seconds",
                "hard_idle_after_seconds",
                "attention_active_weight",
                "attention_low_activity_weight",
                "attention_gap_weight",
                "attention_interrupted_weight",
                "attention_hard_idle_weight",
                "raw_retention_days",
            ):
                raw.pop(key)
            (data_dir / "config.json").write_text(
                json.dumps(raw),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"FOCUSLEDGER_DATA_DIR": str(data_dir)},
                clear=False,
            ):
                config = load_config()

            self.assertEqual(config.low_activity_after_seconds, 180)
            self.assertEqual(config.attention_gap_after_seconds, 600)
            self.assertEqual(config.interrupted_after_seconds, 1200)
            self.assertEqual(config.hard_idle_after_seconds, 1800)
            self.assertEqual(config.attention_low_activity_weight, 0.5)
            self.assertEqual(config.raw_retention_days, 7)

    def test_parser_accepts_raw_retention_days(self) -> None:
        args = build_parser().parse_args(
            ["config", "set", "raw-retention-days", "14"]
        )
        self.assertEqual(args.days, 14)

    def test_retention_date_uses_system_local_timezone(self) -> None:
        now = datetime(2026, 7, 25, 2, 0, tzinfo=timezone.utc)
        local_date = _system_local_date(
            now,
            timezone(timedelta(hours=-4)),
        )
        self.assertEqual(local_date.isoformat(), "2026-07-24")

    def test_render_attention_timeline_compacts_segments(self) -> None:
        base = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
        states = [
            ("active", 1.0),
            ("low_activity", 0.5),
            ("attention_gap", 0.0),
            ("interrupted", 0.0),
            ("hard_idle", 0.0),
        ]
        segments = []
        for index, (state, weight) in enumerate(states):
            started_at = base + timedelta(minutes=index)
            ended_at = started_at + timedelta(minutes=1)
            segments.append(
                {
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "attention_state": state,
                    "attention_weight": weight,
                }
            )

        lines = render_attention_timeline(segments, width=10, charset="unicode")

        self.assertIn("09:00", lines[0])
        self.assertIn("09:05", lines[0])
        self.assertIn("\u2588", lines[1])
        self.assertIn("\u2592", lines[1])
        self.assertIn("\u2591", lines[1])
        self.assertIn("!", lines[1])
        self.assertIn("\u00b7", lines[1])


if __name__ == "__main__":
    unittest.main()
