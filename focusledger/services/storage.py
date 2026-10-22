"""How much room the local database takes, and what is in it.

SQLite here has no per-table size view (dbstat is not compiled in), so the
summary reports what can be measured exactly: the file and its WAL, pages in
use versus pages freed but not yet returned to the disk, and row counts per
layer. That is enough to tell whether archiving or compacting would help.
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from focusledger.services.retention import pending_archive_dates


def storage_summary(conn: Connection, config) -> dict[str, object]:
    path = Path(config.database_path)
    page_size = _pragma(conn, "page_size")
    page_count = _pragma(conn, "page_count")
    free_pages = _pragma(conn, "freelist_count")

    raw = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT local_date), MIN(local_date), MAX(local_date)
        FROM raw_activity_events
        """
    ).fetchone()
    archived = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT local_date), MIN(local_date), MAX(local_date)
        FROM activity_segments
        """
    ).fetchone()
    last_archive = conn.execute(
        "SELECT value FROM app_metadata WHERE key = 'last_retention_run_at'"
    ).fetchone()

    return {
        "path": str(path),
        "file_bytes": _size(path),
        "wal_bytes": _size(Path(f"{path}-wal")),
        "used_bytes": (page_count - free_pages) * page_size,
        "free_bytes": free_pages * page_size,
        "schema_version": _pragma(conn, "user_version"),
        "raw": {
            "rows": raw[0],
            "days": raw[1],
            "first_date": raw[2],
            "last_date": raw[3],
            "retention_days": config.raw_retention_days,
            "pending_archive_days": len(pending_archive_dates(conn, config.raw_retention_days)),
        },
        "archived": {
            "days": archived[1],
            "first_date": archived[2],
            "last_date": archived[3],
            "activity_segments": archived[0],
            "attention_segments": _count(conn, "attention_segments"),
        },
        "heartbeats": _count(conn, "agent_heartbeats"),
        "applications": _count(conn, "applications"),
        "last_archive_run_at": last_archive[0] if last_archive else None,
    }


def _pragma(conn: Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def _count(conn: Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0
