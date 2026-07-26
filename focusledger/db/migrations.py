from __future__ import annotations

from importlib.resources import files
from sqlite3 import Connection


SCHEMA_VERSION = "8"


def apply_migrations(conn: Connection) -> None:
    # This setting must be selected before a new database gets its first table.
    # Existing databases retain their current mode until a full VACUUM, while
    # still reusing pages released by retention.
    has_tables = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
    ).fetchone()
    if not has_tables:
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")

    schema = files("focusledger.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    # Columns added after the first release: CREATE TABLE IF NOT EXISTS leaves
    # existing tables untouched, so add them explicitly for older databases.
    _ensure_column(conn, "raw_activity_events", "idle_seconds", "REAL")
    _ensure_column(conn, "raw_activity_events", "captured_at_utc", "TEXT")
    _ensure_column(conn, "raw_activity_events", "local_date", "TEXT")
    _ensure_column(conn, "raw_activity_events", "timezone_id", "TEXT")
    _ensure_column(conn, "raw_activity_events", "timezone_name", "TEXT")
    _ensure_column(conn, "raw_activity_events", "utc_offset_seconds", "INTEGER")
    _ensure_column(conn, "raw_activity_events", "is_dst", "INTEGER")
    _ensure_column(conn, "raw_activity_events", "local_day_start_utc", "TEXT")
    _ensure_column(conn, "activity_segments", "local_date", "TEXT")
    _ensure_column(conn, "activity_segments", "started_at_utc", "TEXT")
    _ensure_column(conn, "activity_segments", "ended_at_utc", "TEXT")
    _ensure_column(conn, "activity_segments", "timezone_id", "TEXT")
    _ensure_column(conn, "activity_segments", "timezone_name", "TEXT")
    _ensure_column(conn, "activity_segments", "utc_offset_seconds", "INTEGER")
    _ensure_column(conn, "activity_segments", "is_dst", "INTEGER")
    _ensure_column(conn, "activity_segments", "local_day_start_utc", "TEXT")
    _ensure_column(conn, "attention_segments", "local_date", "TEXT")
    _ensure_column(conn, "attention_segments", "started_at_utc", "TEXT")
    _ensure_column(conn, "attention_segments", "ended_at_utc", "TEXT")
    _ensure_column(conn, "attention_segments", "timezone_id", "TEXT")
    _ensure_column(conn, "attention_segments", "timezone_name", "TEXT")
    _ensure_column(conn, "attention_segments", "utc_offset_seconds", "INTEGER")
    _ensure_column(conn, "attention_segments", "is_dst", "INTEGER")
    _ensure_column(conn, "attention_segments", "local_day_start_utc", "TEXT")
    conn.execute(
        """
        UPDATE raw_activity_events
        SET
            local_date = COALESCE(local_date, substr(captured_at, 1, 10)),
            captured_at_utc = COALESCE(
                captured_at_utc,
                strftime('%Y-%m-%dT%H:%M:%f+00:00', captured_at)
            )
        WHERE local_date IS NULL OR captured_at_utc IS NULL
        """
    )
    conn.execute(
        """
        UPDATE activity_segments
        SET local_date = substr(started_at, 1, 10)
        WHERE local_date IS NULL
        """
    )
    conn.execute(
        """
        UPDATE attention_segments
        SET local_date = substr(started_at, 1, 10)
        WHERE local_date IS NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_activity_segments_local_date
        ON activity_segments(local_date, started_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attention_segments_local_date
        ON attention_segments(local_date, started_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_raw_activity_events_local_date_utc
        ON raw_activity_events(local_date, captured_at_utc)
        """
    )
    # Per-app attention is calculated by intersecting the two cooked segment
    # streams. Remove the redundant v6 summary table if it is present.
    conn.execute("DROP TABLE IF EXISTS daily_app_attention")
    conn.execute(
        """
        INSERT INTO app_metadata(key, value, updated_at)
        VALUES ('schema_version', ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (SCHEMA_VERSION,),
    )
    conn.commit()


def _ensure_column(conn: Connection, table: str, column: str, decl: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
