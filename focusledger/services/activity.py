from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from sqlite3 import Connection

from focusledger.models import ActiveWindowSnapshot
from focusledger.services.attention import (
    ATTENTION_STATES,
    UNKNOWN,
    AttentionPolicy,
    AttentionResult,
    classify_attention,
)
from focusledger.services.privacy import sanitize_snapshot


DEFAULT_SEGMENT_GAP_SECONDS = 15


def record_activity_snapshot(
    conn: Connection,
    *,
    device_id: str,
    snapshot: ActiveWindowSnapshot,
    segment_gap_seconds: int,
    idle_seconds: float | None = None,
    attention_policy: AttentionPolicy | None = None,
) -> None:
    snapshot = sanitize_snapshot(snapshot)
    captured_at = snapshot.captured_at.isoformat()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO raw_activity_events(
            id, device_id, captured_at, process_id, process_name, executable_path,
            window_title, window_handle, source, idle_seconds, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            idle_seconds,
            now,
        ),
    )
    _upsert_application(conn, snapshot, now)
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


def list_timeline(conn: Connection, day: date) -> list[dict[str, object]]:
    return _derive_activity_segments(_list_raw_activity_events(conn, day))


def summarize_by_app(conn: Connection, day: date) -> list[dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for segment in list_timeline(conn, day):
        process_name = str(segment["process_name"] or "(unknown)")
        bucket = summary.setdefault(
            process_name,
            {
                "process_name": process_name,
                "total_seconds": 0.0,
                "segment_count": 0,
            },
        )
        bucket["total_seconds"] = float(bucket["total_seconds"]) + float(
            segment["duration_seconds"] or 0.0
        )
        bucket["segment_count"] = int(bucket["segment_count"]) + 1

    rows = list(summary.values())
    rows.sort(key=lambda row: float(row["total_seconds"]), reverse=True)
    return rows


def list_attention_segments(
    conn: Connection,
    day: date,
    attention_policy: AttentionPolicy | None = None,
) -> list[dict[str, object]]:
    return _derive_attention_segments(
        _list_raw_activity_events(conn, day),
        attention_policy or AttentionPolicy(),
    )


def summarize_attention(
    conn: Connection,
    day: date,
    poll_interval_seconds: int,
    attention_policy: AttentionPolicy | None = None,
) -> dict[str, object]:
    """Estimate attention-state time from keyboard/mouse idle samples."""
    policy = attention_policy or AttentionPolicy()
    all_states = (*ATTENTION_STATES, UNKNOWN)
    totals = {state: 0.0 for state in all_states}
    per_app: dict[str, dict[str, float]] = {}
    effective_seconds = 0.0

    raw_rows = _list_raw_activity_events(conn, day)
    attention_segments = _derive_attention_segments(raw_rows, policy)
    for segment in attention_segments:
        state = str(segment["attention_state"])
        seconds = float(segment["duration_seconds"] or 0.0)
        state_weight = float(segment["attention_weight"] or 0.0)
        totals[state] += seconds
        effective_seconds += seconds * state_weight

    weight = float(poll_interval_seconds)
    # Per-app attention is a sample-level approximation because app ownership is
    # stored on raw events, while attention intervals can span multiple apps.
    rows = raw_rows
    for row in rows:
        name = row["process_name"] or "(unknown)"
        result = classify_attention(row["idle_seconds"], policy)
        state = result.state
        state_weight = result.weight

        bucket = per_app.setdefault(
            name,
            {
                **{state_name: 0.0 for state_name in all_states},
                "raw_seconds": 0.0,
                "effective_seconds": 0.0,
            },
        )
        bucket[state] += weight
        bucket["raw_seconds"] += weight
        bucket["effective_seconds"] += weight * state_weight

    per_app_rows = [
        {
            "process_name": name,
            **values,
        }
        for name, values in per_app.items()
    ]
    per_app_rows.sort(
        key=lambda row: row["raw_seconds"],
        reverse=True,
    )

    return {
        **{f"{state}_seconds": seconds for state, seconds in totals.items()},
        "raw_seconds": sum(totals.values()),
        "effective_seconds": effective_seconds,
        "per_app": per_app_rows,
    }


def _list_raw_activity_events(conn: Connection, day: date) -> list[dict[str, object]]:
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day, time.max).astimezone()
    rows = conn.execute(
        """
        SELECT
            device_id,
            captured_at,
            process_name,
            executable_path,
            window_title,
            source,
            idle_seconds
        FROM raw_activity_events
        WHERE captured_at >= ? AND captured_at <= ?
        ORDER BY captured_at ASC
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _derive_activity_segments(
    rows: list[dict[str, object]],
    segment_gap_seconds: int = DEFAULT_SEGMENT_GAP_SECONDS,
) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    previous_at: datetime | None = None

    for row in rows:
        captured_at = datetime.fromisoformat(str(row["captured_at"]))
        key = _activity_key(row)
        if current is None:
            current = _new_activity_segment(row, captured_at)
            previous_at = captured_at
            continue

        assert previous_at is not None
        gap_seconds = max(0.0, (captured_at - previous_at).total_seconds())
        current_key = _activity_key(current)
        if key == current_key and gap_seconds <= segment_gap_seconds:
            current["ended_at"] = captured_at.isoformat()
            current["duration_seconds"] = max(
                0.0,
                (
                    captured_at
                    - datetime.fromisoformat(str(current["started_at"]))
                ).total_seconds(),
            )
        else:
            segments.append(current)
            current = _new_activity_segment(row, captured_at)
        previous_at = captured_at

    if current is not None:
        segments.append(current)
    return segments


def _activity_key(row: dict[str, object]) -> tuple[object, object, object, object]:
    return (
        row["process_name"] or "(unknown)",
        row["window_title"] or "",
        row["executable_path"],
        row["source"],
    )


def _new_activity_segment(
    row: dict[str, object],
    captured_at: datetime,
) -> dict[str, object]:
    return {
        "started_at": captured_at.isoformat(),
        "ended_at": captured_at.isoformat(),
        "duration_seconds": 0.0,
        "process_name": row["process_name"] or "(unknown)",
        "window_title": row["window_title"] or "",
        "executable_path": row["executable_path"],
        "source": row["source"],
    }


def _derive_attention_segments(
    rows: list[dict[str, object]],
    policy: AttentionPolicy,
    segment_gap_seconds: int = DEFAULT_SEGMENT_GAP_SECONDS,
) -> list[dict[str, object]]:
    if not rows:
        return []

    output: list[dict[str, object]] = []
    active_input_at: datetime | None = None
    observed_until: datetime | None = None

    for row in rows:
        captured_at = datetime.fromisoformat(str(row["captured_at"]))
        idle_seconds = row["idle_seconds"]
        if idle_seconds is None:
            attention = classify_attention(None, policy)
            _append_attention_segment(
                output,
                captured_at,
                captured_at,
                attention,
                None,
                segment_gap_seconds,
            )
            continue

        idle = max(0.0, float(idle_seconds))
        current_input_at = captured_at - timedelta(seconds=idle)
        if active_input_at is None:
            active_input_at = current_input_at
            observed_until = captured_at
            continue

        assert observed_until is not None
        if current_input_at > active_input_at:
            _append_finalized_idle_interval(
                output,
                active_input_at,
                current_input_at,
                policy,
                segment_gap_seconds,
            )
            active_input_at = current_input_at
            observed_until = captured_at
        else:
            observed_until = max(observed_until, captured_at)

    if active_input_at is not None and observed_until is not None:
        _append_tentative_idle_interval(
            output,
            active_input_at,
            observed_until,
            policy,
            segment_gap_seconds,
        )
    return output


def _append_finalized_idle_interval(
    output: list[dict[str, object]],
    started_at: datetime,
    ended_at: datetime,
    policy: AttentionPolicy,
    segment_gap_seconds: int,
) -> None:
    duration = max(0.0, (ended_at - started_at).total_seconds())
    if duration >= policy.hard_idle_after_seconds:
        attention = classify_attention(policy.hard_idle_after_seconds, policy)
        _append_attention_segment(
            output,
            started_at,
            ended_at,
            attention,
            duration,
            segment_gap_seconds,
        )
        return
    _append_tentative_idle_interval(
        output,
        started_at,
        ended_at,
        policy,
        segment_gap_seconds,
    )


def _append_tentative_idle_interval(
    output: list[dict[str, object]],
    started_at: datetime,
    ended_at: datetime,
    policy: AttentionPolicy,
    segment_gap_seconds: int,
) -> None:
    for range_start, range_end, attention in _iter_attention_ranges(
        started_at,
        started_at,
        ended_at,
        policy,
    ):
        max_idle = max(0.0, (range_end - started_at).total_seconds())
        _append_attention_segment(
            output,
            range_start,
            range_end,
            attention,
            max_idle,
            segment_gap_seconds,
        )


def _iter_attention_ranges(
    last_input_at: datetime,
    start_at: datetime,
    end_at: datetime,
    policy: AttentionPolicy,
) -> list[tuple[datetime, datetime, AttentionResult]]:
    if start_at == end_at:
        idle = max(0.0, (end_at - last_input_at).total_seconds())
        return [(start_at, end_at, classify_attention(idle, policy))]

    thresholds = (
        policy.low_activity_after_seconds,
        policy.attention_gap_after_seconds,
        policy.interrupted_after_seconds,
        policy.hard_idle_after_seconds,
    )
    ranges = []
    cursor = start_at
    while cursor < end_at:
        idle_at_cursor = max(0.0, (cursor - last_input_at).total_seconds())
        attention = classify_attention(idle_at_cursor, policy)
        next_boundary = end_at
        for threshold in thresholds:
            boundary = last_input_at + timedelta(seconds=threshold)
            if cursor < boundary < next_boundary:
                next_boundary = boundary
                break
        ranges.append((cursor, next_boundary, attention))
        cursor = next_boundary

    attention_at_end = classify_attention(
        max(0.0, (end_at - last_input_at).total_seconds()),
        policy,
    )
    if ranges and ranges[-1][2] != attention_at_end:
        ranges.append((end_at, end_at, attention_at_end))
    return ranges


def _append_attention_segment(
    output: list[dict[str, object]],
    started_at: datetime,
    ended_at: datetime,
    attention: AttentionResult,
    max_idle_seconds: float | None,
    segment_gap_seconds: int,
) -> None:
    duration = max(0.0, (ended_at - started_at).total_seconds())
    if output:
        previous = output[-1]
        previous_end = datetime.fromisoformat(str(previous["ended_at"]))
        gap_seconds = max(0.0, (started_at - previous_end).total_seconds())
        same_state = (
            previous["attention_state"] == attention.state
            and float(previous["attention_weight"]) == float(attention.weight)
        )
        if same_state and gap_seconds <= segment_gap_seconds:
            previous["ended_at"] = ended_at.isoformat()
            previous["duration_seconds"] = max(
                0.0,
                (
                    ended_at
                    - datetime.fromisoformat(str(previous["started_at"]))
                ).total_seconds(),
            )
            if max_idle_seconds is not None:
                previous["max_idle_seconds"] = max(
                    float(previous["max_idle_seconds"] or 0.0),
                    max_idle_seconds,
                )
            return

    output.append(
        {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": duration,
            "attention_state": attention.state,
            "attention_weight": attention.weight,
            "max_idle_seconds": max_idle_seconds,
        }
    )


def format_duration(seconds: float | int | None) -> str:
    total = int(seconds or 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
