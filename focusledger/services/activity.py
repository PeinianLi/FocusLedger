from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from sqlite3 import Connection

from focusledger.models import ActiveWindowSnapshot
from focusledger.services.privacy import sanitize_snapshot


def record_activity_snapshot(
    conn: Connection,
    *,
    device_id: str,
    snapshot: ActiveWindowSnapshot,
    segment_gap_seconds: int,
) -> None:
    snapshot = sanitize_snapshot(snapshot)
    captured_at = snapshot.captured_at.isoformat()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO raw_activity_events(
            id, device_id, captured_at, process_id, process_name, executable_path,
            window_title, window_handle, source, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            device_id,
            captured_at,
            snapshot.process_id,
            snapshot.process_name,
            snapshot.executable_path,
            snapshot.window_title,
            snapshot.window_handle,
            snapshot.source,
            now,
        ),
    )
    _upsert_application(conn, snapshot, now)
    _merge_activity_segment(conn, device_id, snapshot, segment_gap_seconds, now)
    conn.commit()


def _upsert_application(conn: Connection, snapshot: ActiveWindowSnapshot, now: str) -> None:
    if not snapshot.process_name:
        return
    conn.execute(
        """
        INSERT INTO applications(id, process_name, executable_path, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(process_name, executable_path)
        DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        (uuid.uuid4().hex, snapshot.process_name, snapshot.executable_path, now, now),
    )


def _merge_activity_segment(
    conn: Connection,
    device_id: str,
    snapshot: ActiveWindowSnapshot,
    segment_gap_seconds: int,
    now: str,
) -> None:
    previous = conn.execute(
        """
        SELECT *
        FROM activity_segments
        WHERE device_id = ?
        ORDER BY ended_at DESC
        LIMIT 1
        """,
        (device_id,),
    ).fetchone()

    captured_at = snapshot.captured_at
    if previous is not None:
        previous_ended = datetime.fromisoformat(previous["ended_at"])
        gap_seconds = max(0.0, (captured_at - previous_ended).total_seconds())
        same_activity = (
            previous["process_name"] == snapshot.process_name
            and previous["window_title"] == snapshot.window_title
            and previous["executable_path"] == snapshot.executable_path
            and previous["source"] == snapshot.source
        )
        if same_activity and gap_seconds <= segment_gap_seconds:
            started_at = datetime.fromisoformat(previous["started_at"])
            duration = max(0.0, (captured_at - started_at).total_seconds())
            conn.execute(
                """
                UPDATE activity_segments
                SET ended_at = ?, duration_seconds = ?, updated_at = ?
                WHERE id = ?
                """,
                (captured_at.isoformat(), duration, now, previous["id"]),
            )
            return

    conn.execute(
        """
        INSERT INTO activity_segments(
            id, device_id, started_at, ended_at, duration_seconds, process_name,
            window_title, executable_path, source, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            device_id,
            captured_at.isoformat(),
            captured_at.isoformat(),
            snapshot.process_name,
            snapshot.window_title,
            snapshot.executable_path,
            snapshot.source,
            now,
            now,
        ),
    )


def list_timeline(conn: Connection, day: date) -> list[dict[str, object]]:
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day, time.max).astimezone()
    rows = conn.execute(
        """
        SELECT started_at, ended_at, duration_seconds, process_name, window_title, executable_path
        FROM activity_segments
        WHERE started_at >= ? AND started_at <= ?
        ORDER BY started_at ASC
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def summarize_by_app(conn: Connection, day: date) -> list[dict[str, object]]:
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day, time.max).astimezone()
    rows = conn.execute(
        """
        SELECT
            COALESCE(process_name, '(unknown)') AS process_name,
            SUM(duration_seconds) AS total_seconds,
            COUNT(*) AS segment_count
        FROM activity_segments
        WHERE started_at >= ? AND started_at <= ?
        GROUP BY COALESCE(process_name, '(unknown)')
        ORDER BY total_seconds DESC
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def format_duration(seconds: float | int | None) -> str:
    total = int(seconds or 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
