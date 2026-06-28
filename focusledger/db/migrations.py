from __future__ import annotations

from importlib.resources import files
from sqlite3 import Connection


SCHEMA_VERSION = "2"


def apply_migrations(conn: Connection) -> None:
    schema = files("focusledger.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    # Columns added after the first release: CREATE TABLE IF NOT EXISTS leaves
    # existing tables untouched, so add them explicitly for older databases.
    _ensure_column(conn, "raw_activity_events", "idle_seconds", "REAL")
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
