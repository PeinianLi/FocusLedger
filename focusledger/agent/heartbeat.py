from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from sqlite3 import Connection


# While its status is unchanged the agent beats at most this often. Every
# sample already lands in raw_activity_events, so beating on each poll only
# doubled the write volume; the heartbeat just has to show liveness, errors
# and stops.
HEARTBEAT_INTERVAL_SECONDS = 30


def heartbeat_stale_after(poll_interval_seconds: int) -> int:
    """Age after which the latest heartbeat no longer proves the agent is up."""
    return max(poll_interval_seconds * 3, HEARTBEAT_INTERVAL_SECONDS * 2)


class HeartbeatThrottle:
    """Write a heartbeat when the status changes or the interval has elapsed."""

    def __init__(
        self,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._last_status: str | None = None
        self._last_written_at: float | None = None

    def beat(
        self,
        conn: Connection,
        *,
        device_id: str,
        status: str = "running",
        message: str | None = None,
    ) -> bool:
        now = self._clock()
        if (
            status == self._last_status
            and self._last_written_at is not None
            and now - self._last_written_at < self._interval_seconds
        ):
            return False
        write_heartbeat(conn, device_id=device_id, status=status, message=message)
        self._last_status = status
        self._last_written_at = now
        return True


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
