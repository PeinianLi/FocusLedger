from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from sqlite3 import Connection

from focusledger.services.activity import (
    _derive_activity_segments,
    _derive_attention_segments,
    _select_raw_events,
)
from focusledger.services.attention import (
    AttentionPolicy,
)


@dataclass(frozen=True)
class RetentionResult:
    archived_days: int = 0
    raw_events_deleted: int = 0
    heartbeats_deleted: int = 0
    activity_segments_written: int = 0
    attention_segments_written: int = 0


def apply_retention(
    conn: Connection,
    *,
    retention_days: int,
    poll_interval_seconds: int,
    attention_policy: AttentionPolicy,
    now: datetime | None = None,
    local_timezone: tzinfo | None = None,
) -> RetentionResult:
    """Archive complete local days outside the raw-data retention window.

    A retention window of seven keeps today plus the previous six local dates.
    Each older date is archived and deleted in one transaction, so a failed
    archive never leaves a day half-converted.
    """
    if retention_days < 1:
        raise ValueError("raw retention days must be at least 1")
    if poll_interval_seconds < 1:
        raise ValueError("poll interval must be at least 1 second")
    attention_policy.validate()

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    keep_from_date = _keep_from_date(current, retention_days, local_timezone)
    archive_dates = _archive_dates(conn, keep_from_date)

    totals = RetentionResult()
    for local_date in archive_dates:
        day_result = _archive_day(
            conn,
            local_date=local_date,
            poll_interval_seconds=poll_interval_seconds,
            attention_policy=attention_policy,
        )
        totals = RetentionResult(
            archived_days=totals.archived_days + day_result.archived_days,
            raw_events_deleted=(
                totals.raw_events_deleted + day_result.raw_events_deleted
            ),
            heartbeats_deleted=totals.heartbeats_deleted,
            activity_segments_written=(
                totals.activity_segments_written
                + day_result.activity_segments_written
            ),
            attention_segments_written=(
                totals.attention_segments_written
                + day_result.attention_segments_written
            ),
        )

    keep_from_naive = datetime.combine(keep_from_date, time.min)
    if local_timezone is None:
        keep_from_local = keep_from_naive.astimezone()
    else:
        keep_from_local = keep_from_naive.replace(tzinfo=local_timezone)
    heartbeat_cutoff = keep_from_local.astimezone(timezone.utc).isoformat()
    cursor = conn.execute(
        "DELETE FROM agent_heartbeats WHERE timestamp < ?",
        (heartbeat_cutoff,),
    )
    heartbeats_deleted = max(0, cursor.rowcount)
    conn.execute(
        """
        INSERT INTO app_metadata(key, value, updated_at)
        VALUES ('last_retention_run_at', ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (current.isoformat(), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    # Fresh databases use incremental auto-vacuum. Existing databases still
    # reuse deleted pages and can be compacted once with the CLI maintenance
    # command if the physical file should shrink immediately.
    # incremental_vacuum frees one page per step, so drain every row; a bare
    # execute() steps once and releases a single page.
    auto_vacuum = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
    if auto_vacuum == 2:
        conn.execute("PRAGMA incremental_vacuum").fetchall()

    return RetentionResult(
        archived_days=totals.archived_days,
        raw_events_deleted=totals.raw_events_deleted,
        heartbeats_deleted=heartbeats_deleted,
        activity_segments_written=totals.activity_segments_written,
        attention_segments_written=totals.attention_segments_written,
    )


def pending_archive_dates(
    conn: Connection,
    retention_days: int,
    now: datetime | None = None,
    local_timezone: tzinfo | None = None,
) -> list[date]:
    """Local dates past the raw retention window that are not archived yet.

    Archiving runs from the agent loop, so these pile up while it is stopped.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return _archive_dates(conn, _keep_from_date(current, retention_days, local_timezone))


def _keep_from_date(
    current: datetime,
    retention_days: int,
    local_timezone: tzinfo | None,
) -> date:
    local_now = _as_system_local(current, local_timezone)
    return local_now.date() - timedelta(days=retention_days - 1)


def _archive_dates(conn: Connection, keep_from_date: date) -> list[date]:
    rows = conn.execute(
        """
        SELECT DISTINCT local_date
        FROM raw_activity_events
        WHERE local_date < ?
        ORDER BY local_date
        """,
        (keep_from_date.isoformat(),),
    ).fetchall()
    return [date.fromisoformat(str(row["local_date"])) for row in rows]


def _archive_day(
    conn: Connection,
    *,
    local_date: date,
    poll_interval_seconds: int,
    attention_policy: AttentionPolicy,
) -> RetentionResult:
    rows = _select_raw_events(
        conn,
        "r.local_date = ?",
        (local_date.isoformat(),),
        "r.device_id, r.captured_at_utc",
    )
    if not rows:
        return RetentionResult()

    activity_segments: list[dict[str, object]] = []
    attention_segments: list[dict[str, object]] = []
    by_device: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_device.setdefault(str(row["device_id"]), []).append(row)

    for device_id, device_rows in by_device.items():
        activity_segments.extend(_derive_activity_segments(device_rows))
        device_attention = _derive_attention_segments(
            device_rows,
            attention_policy,
        )
        for segment in device_attention:
            segment["device_id"] = device_id
        attention_segments.extend(device_attention)

    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        date_text = local_date.isoformat()
        conn.execute(
            "DELETE FROM activity_segments WHERE local_date = ?",
            (date_text,),
        )
        conn.execute(
            "DELETE FROM attention_segments WHERE local_date = ?",
            (date_text,),
        )
        conn.executemany(
            """
            INSERT INTO activity_segments(
                id, local_date, device_id, started_at, ended_at,
                started_at_utc, ended_at_utc, timezone_id, timezone_name,
                utc_offset_seconds, is_dst, local_day_start_utc,
                duration_seconds, process_name, window_title, executable_path,
                source, created_at, updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    uuid.uuid4().hex,
                    date_text,
                    str(segment.get("device_id") or ""),
                    str(segment["started_at"]),
                    str(segment["ended_at"]),
                    segment.get("started_at_utc"),
                    segment.get("ended_at_utc"),
                    segment.get("timezone_id"),
                    segment.get("timezone_name"),
                    segment.get("utc_offset_seconds"),
                    segment.get("is_dst"),
                    segment.get("local_day_start_utc"),
                    float(segment["duration_seconds"] or 0.0),
                    segment["process_name"],
                    segment["window_title"],
                    segment["executable_path"],
                    str(segment["source"]),
                    generated_at,
                    generated_at,
                )
                for segment in activity_segments
            ],
        )
        conn.executemany(
            """
            INSERT INTO attention_segments(
                id, local_date, device_id, started_at, ended_at,
                started_at_utc, ended_at_utc, timezone_id, timezone_name,
                utc_offset_seconds, is_dst, local_day_start_utc,
                duration_seconds, attention_state, attention_weight,
                max_idle_seconds, source, created_at, updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    uuid.uuid4().hex,
                    date_text,
                    str(segment["device_id"]),
                    str(segment["started_at"]),
                    str(segment["ended_at"]),
                    segment.get("started_at_utc"),
                    segment.get("ended_at_utc"),
                    segment.get("timezone_id"),
                    segment.get("timezone_name"),
                    segment.get("utc_offset_seconds"),
                    segment.get("is_dst"),
                    segment.get("local_day_start_utc"),
                    float(segment["duration_seconds"] or 0.0),
                    str(segment["attention_state"]),
                    float(segment["attention_weight"] or 0.0),
                    segment["max_idle_seconds"],
                    "retention_archive",
                    generated_at,
                    generated_at,
                )
                for segment in attention_segments
            ],
        )
        deleted = conn.execute(
            "DELETE FROM raw_activity_events WHERE local_date = ?",
            (date_text,),
        ).rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return RetentionResult(
        archived_days=1,
        raw_events_deleted=max(0, deleted),
        activity_segments_written=len(activity_segments),
        attention_segments_written=len(attention_segments),
    )


def _as_system_local(
    moment: datetime,
    local_timezone: tzinfo | None = None,
) -> datetime:
    if local_timezone is None:
        return moment.astimezone()
    return moment.astimezone(local_timezone)
