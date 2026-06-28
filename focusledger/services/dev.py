from __future__ import annotations

from pathlib import Path

from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations


def reset_database(database_path: Path) -> list[Path]:
    """Delete the local SQLite database (and its WAL sidecar files) and recreate
    a fresh schema. Returns the list of files that were actually deleted.
    """
    deleted: list[Path] = []
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{database_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
            deleted.append(candidate)

    with connect(database_path) as conn:
        apply_migrations(conn)

    return deleted
