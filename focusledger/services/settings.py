from __future__ import annotations

from datetime import datetime, timezone
from sqlite3 import Connection


def set_setting(conn: Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_setting(conn: Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"])
