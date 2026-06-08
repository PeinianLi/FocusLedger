from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from sqlite3 import Connection


def write_heartbeat(
    conn: Connection,
    *,
    device_id: str,
    status: str = "running",
    message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_heartbeats(id, device_id, timestamp, pid, status, message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            device_id,
            datetime.now(timezone.utc).isoformat(),
            os.getpid(),
            status,
            message,
        ),
    )
    conn.commit()


def latest_heartbeat(conn: Connection) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT device_id, timestamp, pid, status, message
        FROM agent_heartbeats
        ORDER BY timestamp DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None
