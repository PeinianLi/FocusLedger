from __future__ import annotations

from importlib.resources import files
from sqlite3 import Connection


SCHEMA_VERSION = "1"


def apply_migrations(conn: Connection) -> None:
    schema = files("focusledger.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.execute(
        """
        INSERT INTO app_metadata(key, value, updated_at)
        VALUES ('schema_version', ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (SCHEMA_VERSION,),
    )
    conn.commit()
