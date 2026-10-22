from __future__ import annotations

import logging
from importlib.resources import files
from sqlite3 import Connection


# The schema version lives in SQLite's own user_version header field. Each
# step below runs once, in order, for databases older than its number.
SCHEMA_VERSION = 10

LOGGER = logging.getLogger(__name__)


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer FocusLedger than this one."""


def apply_migrations(conn: Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"database schema v{version} is newer than this FocusLedger "
            f"(v{SCHEMA_VERSION}); update FocusLedger before using this database"
        )
    if version == SCHEMA_VERSION:
        return

    # This setting must be selected before a new database gets its first table.
    # Existing databases retain their current mode until a full VACUUM, while
    # still reusing pages released by retention.
    has_tables = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
    ).fetchone()
    if not has_tables:
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")

    for target, step in _STEPS:
        if version < target:
            step(conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
            version = target
    conn.execute(
        """
        INSERT INTO app_metadata(key, value, updated_at)
        VALUES ('schema_version', ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _baseline_v9(conn: Connection) -> None:
    """Bring a database from before version numbers (user_version 0) to v9.

    Releases up to v9 kept no version, so this step works from what it finds:
    the schema script fills in missing tables and the checks below upgrade
    older table shapes in place. It is safe to run on any of them.
    """
    schema = files("focusledger.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)

    legacy_raw = "captured_at" in _columns(conn, "raw_activity_events")
    legacy_applications = "last_seen_at" in _columns(conn, "applications")
    if legacy_raw:
        _upgrade_legacy_raw_columns(conn)
    if legacy_raw or legacy_applications:
        _rebuild_raw_storage(conn, legacy_raw=legacy_raw)

    # Columns added after the first release: CREATE TABLE IF NOT EXISTS leaves
    # existing tables untouched, so add them explicitly for older databases.
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
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_identity
        ON applications(process_name, executable_path)
        """
    )
    # Per-app attention is calculated by intersecting the two cooked segment
    # streams. Remove the redundant v6 summary table if it is present.
    conn.execute("DROP TABLE IF EXISTS daily_app_attention")
    conn.commit()


def _migrate_v10(conn: Connection) -> None:
    """Key workdays by date alone and give tasks and pauses foreign keys."""
    # Replaced by the workday_key-only index from the schema script: a session
    # keyed per device let a regenerated device_id open a second copy of a day.
    conn.execute("DROP INDEX IF EXISTS idx_workday_sessions_key")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_workday_sessions_workday_key
        ON workday_sessions(workday_key)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_classification_rules_target
        ON classification_rules(match_type, pattern)
        """
    )
    conn.commit()

    # SQLite only adds a foreign key by rebuilding the table.
    rebuilds = [
        (table, ddl)
        for table, ddl in (("tasks", _TASKS_V10), ("workday_pauses", _WORKDAY_PAUSES_V10))
        if not conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    ]
    if rebuilds:
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            # A reference to a row that no longer exists would break the new key.
            conn.execute(
                """
                UPDATE tasks SET deliverable_id = NULL
                WHERE deliverable_id NOT IN (SELECT id FROM deliverables)
                """
            )
            conn.execute(
                """
                DELETE FROM workday_pauses
                WHERE workday_id NOT IN (SELECT id FROM workday_sessions)
                """
            )
            for table, ddl in rebuilds:
                conn.execute(ddl.format(table=f"{table}_v10"))
                columns = ", ".join(
                    column
                    for column in _column_order(conn, f"{table}_v10")
                    if column in _columns(conn, table)
                )
                conn.execute(
                    f"INSERT INTO {table}_v10({columns}) SELECT {columns} FROM {table}"
                )
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {table}_v10 RENAME TO {table}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    # Dropping a table drops its indexes; recreate them for rebuilt tables.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_deliverable ON tasks(deliverable_id)")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workday_pauses_workday
        ON workday_pauses(workday_id, started_at)
        """
    )
    conn.commit()


_TASKS_V10 = """
    CREATE TABLE {table} (
        id TEXT PRIMARY KEY,
        deliverable_id TEXT REFERENCES deliverables(id),
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        estimate_minutes INTEGER,
        blocker TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""

_WORKDAY_PAUSES_V10 = """
    CREATE TABLE {table} (
        id TEXT PRIMARY KEY,
        workday_id TEXT NOT NULL REFERENCES workday_sessions(id),
        started_at TEXT NOT NULL,
        ended_at TEXT,
        reason TEXT,
        created_at TEXT NOT NULL
    )
"""


def _upgrade_legacy_raw_columns(conn: Connection) -> None:
    """Bring a pre-v9 raw table up to the v8 columns the v9 rebuild reads."""
    _ensure_column(conn, "raw_activity_events", "idle_seconds", "REAL")
    _ensure_column(conn, "raw_activity_events", "captured_at_utc", "TEXT")
    _ensure_column(conn, "raw_activity_events", "local_date", "TEXT")
    _ensure_column(conn, "raw_activity_events", "timezone_id", "TEXT")
    _ensure_column(conn, "raw_activity_events", "timezone_name", "TEXT")
    _ensure_column(conn, "raw_activity_events", "utc_offset_seconds", "INTEGER")
    _ensure_column(conn, "raw_activity_events", "is_dst", "INTEGER")
    _ensure_column(conn, "raw_activity_events", "local_day_start_utc", "TEXT")
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
    # v9 rebuilds the local capture time from UTC plus this offset, so derive
    # it from the stored local timestamp for rows captured before v7.
    conn.execute(
        """
        UPDATE raw_activity_events
        SET utc_offset_seconds = CAST(round(
            (julianday(substr(captured_at, 1, 19)) - julianday(captured_at_utc))
            * 86400
        ) AS INTEGER)
        WHERE utc_offset_seconds IS NULL AND captured_at_utc IS NOT NULL
        """
    )
    conn.commit()


def _rebuild_raw_storage(conn: Connection, *, legacy_raw: bool) -> None:
    """Move pre-v9 raw samples and applications into the v9 tables.

    Runs as one transaction, so an interrupted upgrade leaves the old tables in
    place and the next start simply tries again. Foreign keys are switched off
    while the tables are swapped, as SQLite's table-rebuild procedure requires.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE applications_v9 (
                id INTEGER PRIMARY KEY,
                process_name TEXT,
                executable_path TEXT,
                first_seen_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO applications_v9(process_name, executable_path, first_seen_at)
            SELECT process_name, executable_path, MIN(first_seen_at)
            FROM applications
            GROUP BY process_name, executable_path
            """
        )
        moved = 0
        if legacy_raw:
            conn.execute(
                """
                INSERT INTO applications_v9(process_name, executable_path, first_seen_at)
                SELECT seen.process_name, seen.executable_path, seen.first_seen_at
                FROM (
                    SELECT process_name, executable_path,
                           MIN(captured_at_utc) AS first_seen_at
                    FROM raw_activity_events
                    WHERE process_name IS NOT NULL OR executable_path IS NOT NULL
                    GROUP BY process_name, executable_path
                ) AS seen
                WHERE NOT EXISTS (
                    SELECT 1 FROM applications_v9 AS a
                    WHERE a.process_name IS seen.process_name
                      AND a.executable_path IS seen.executable_path
                )
                """
            )
            conn.execute(
                """
                INSERT INTO time_contexts(
                    timezone_id, timezone_name, utc_offset_seconds, is_dst,
                    local_day_start_utc
                )
                SELECT seen.timezone_id, seen.timezone_name,
                       seen.utc_offset_seconds, seen.is_dst,
                       seen.local_day_start_utc
                FROM (
                    SELECT DISTINCT timezone_id, timezone_name,
                           utc_offset_seconds, is_dst, local_day_start_utc
                    FROM raw_activity_events
                ) AS seen
                WHERE NOT EXISTS (
                    SELECT 1 FROM time_contexts AS t
                    WHERE t.timezone_id IS seen.timezone_id
                      AND t.timezone_name IS seen.timezone_name
                      AND t.utc_offset_seconds IS seen.utc_offset_seconds
                      AND t.is_dst IS seen.is_dst
                      AND t.local_day_start_utc IS seen.local_day_start_utc
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE raw_activity_events_v9 (
                    id INTEGER PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    captured_at_utc TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    time_context_id INTEGER REFERENCES time_contexts(id),
                    process_id INTEGER,
                    application_id INTEGER REFERENCES applications(id),
                    window_title TEXT,
                    window_handle TEXT,
                    source TEXT NOT NULL,
                    idle_seconds REAL
                )
                """
            )
            moved = conn.execute(
                """
                INSERT INTO raw_activity_events_v9(
                    device_id, captured_at_utc, local_date, time_context_id,
                    process_id, application_id, window_title, window_handle,
                    source, idle_seconds
                )
                SELECT
                    r.device_id,
                    COALESCE(r.captured_at_utc, r.captured_at),
                    COALESCE(r.local_date, substr(r.captured_at, 1, 10)),
                    t.id,
                    r.process_id,
                    a.id,
                    r.window_title,
                    r.window_handle,
                    r.source,
                    r.idle_seconds
                FROM raw_activity_events AS r
                LEFT JOIN time_contexts AS t
                  ON t.timezone_id IS r.timezone_id
                 AND t.timezone_name IS r.timezone_name
                 AND t.utc_offset_seconds IS r.utc_offset_seconds
                 AND t.is_dst IS r.is_dst
                 AND t.local_day_start_utc IS r.local_day_start_utc
                LEFT JOIN applications_v9 AS a
                  ON a.process_name IS r.process_name
                 AND a.executable_path IS r.executable_path
                ORDER BY COALESCE(r.captured_at_utc, r.captured_at)
                """
            ).rowcount
            conn.execute("DROP TABLE raw_activity_events")
            conn.execute(
                "ALTER TABLE raw_activity_events_v9 RENAME TO raw_activity_events"
            )
        conn.execute("DROP TABLE applications")
        conn.execute("ALTER TABLE applications_v9 RENAME TO applications")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    LOGGER.info("rebuilt raw storage for schema v9: %s samples moved", moved)
    # Fresh databases use incremental auto-vacuum, so hand the old table's
    # pages back to the file system now instead of waiting for retention. The
    # pragma frees one page per step, so every row has to be drained.
    if conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2:
        conn.execute("PRAGMA incremental_vacuum").fetchall()


def _columns(conn: Connection, table: str) -> set[str]:
    return set(_column_order(conn, table))


def _column_order(conn: Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _ensure_column(conn: Connection, table: str, column: str, decl: str) -> None:
    existing = _columns(conn, table)
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


_STEPS = (
    (9, _baseline_v9),
    (10, _migrate_v10),
)
