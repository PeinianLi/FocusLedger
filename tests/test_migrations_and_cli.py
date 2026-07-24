from __future__ import annotations

import sqlite3
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from focusledger.cli import build_parser, heartbeat_age_seconds, render_attention_timeline
from focusledger.config import build_default_config, load_config
from focusledger.db.connection import connect
from focusledger.db.migrations import SCHEMA_VERSION, apply_migrations


class MigrationAndCliTests(unittest.TestCase):
    def test_v1_database_adds_idle_seconds_column(self) -> None:
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
            conn.close()

            self.assertIn("idle_seconds", columns)
            self.assertNotIn("attention_state", columns)
            self.assertNotIn("attention_weight", columns)
            self.assertIsNone(attention_table)
            self.assertEqual(version, SCHEMA_VERSION)

    def test_start_parser_accepts_background(self) -> None:
        args = build_parser().parse_args(["start", "--background"])
        self.assertTrue(args.background)
        self.assertFalse(args.background_child)

    def test_old_running_heartbeat_is_stale_by_age(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.assertGreater(heartbeat_age_seconds(old.isoformat()), 299)

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
