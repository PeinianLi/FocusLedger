from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
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
from focusledger.time_context import capture_time_context


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
    time_context = capture_time_context(snapshot.captured_at)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO raw_activity_events(
            id, device_id, captured_at, captured_at_utc, local_date,
            timezone_id, timezone_name, utc_offset_seconds, is_dst,
            local_day_start_utc, process_id, process_name, executable_path,
            window_title, window_handle, source, idle_seconds, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            device_id,
            time_context.captured_at,
            time_context.captured_at_utc,
            time_context.local_date,
            time_context.timezone_id,
            time_context.timezone_name,
            time_context.utc_offset_seconds,
            int(time_context.is_dst),
            time_context.local_day_start_utc,
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
    raw_rows = _list_raw_activity_events(conn, day)
    if raw_rows:
        return _derive_activity_segments(raw_rows)
    return _list_archived_activity_segments(conn, day)


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
    raw_rows = _list_raw_activity_events(conn, day)
    if raw_rows:
        return _derive_attention_segments(
            raw_rows,
            attention_policy or AttentionPolicy(),
        )
    return _list_archived_attention_segments(conn, day)


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
    if raw_rows:
        attention_segments = _derive_attention_segments(raw_rows, policy)
    else:
        attention_segments = _list_archived_attention_segments(conn, day)
    for segment in attention_segments:
        state = str(segment["attention_state"])
        seconds = float(segment["duration_seconds"] or 0.0)
        state_weight = float(segment["attention_weight"] or 0.0)
        totals[state] += seconds
        effective_seconds += seconds * state_weight

    if raw_rows:
        weight = float(poll_interval_seconds)
        # Raw dates keep sample-level application ownership.
        for row in raw_rows:
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
    else:
        per_app_rows = _summarize_archived_attention_by_app(
            _list_archived_activity_segments(conn, day),
            attention_segments,
        )
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
    rows = conn.execute(
        """
        SELECT
            device_id,
            captured_at,
            captured_at_utc,
            local_date,
            timezone_id,
            timezone_name,
            utc_offset_seconds,
            is_dst,
            local_day_start_utc,
            process_name,
            executable_path,
            window_title,
            source,
            idle_seconds
        FROM raw_activity_events
        WHERE local_date = ?
           OR (local_date IS NULL AND substr(captured_at, 1, 10) = ?)
        ORDER BY COALESCE(captured_at_utc, captured_at) ASC
        """,
        (day.isoformat(), day.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _list_archived_activity_segments(
    conn: Connection,
    day: date,
) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT
            device_id,
            started_at,
            ended_at,
            started_at_utc,
            ended_at_utc,
            timezone_id,
            timezone_name,
            utc_offset_seconds,
            is_dst,
            local_day_start_utc,
            duration_seconds,
            process_name,
            executable_path,
            window_title,
            source
        FROM activity_segments
        WHERE local_date = ?
        ORDER BY COALESCE(started_at_utc, started_at) ASC
        """,
        (day.isoformat(),),
    ).fetchall()
    return [dict(row) for row in rows]


def _list_archived_attention_segments(
    conn: Connection,
    day: date,
) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT
            device_id,
            started_at,
            ended_at,
            started_at_utc,
            ended_at_utc,
            timezone_id,
            timezone_name,
            utc_offset_seconds,
            is_dst,
            local_day_start_utc,
            duration_seconds,
            attention_state,
            attention_weight,
            max_idle_seconds,
            source
        FROM attention_segments
        WHERE local_date = ?
        ORDER BY COALESCE(started_at_utc, started_at) ASC
        """,
        (day.isoformat(),),
    ).fetchall()
    return [dict(row) for row in rows]


def _summarize_archived_attention_by_app(
    activity_segments: list[dict[str, object]],
    attention_segments: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Calculate per-app attention from the two cooked segment streams."""
    all_states = (*ATTENTION_STATES, UNKNOWN)
    activity_by_device: dict[str, list[dict[str, object]]] = {}
    for segment in activity_segments:
        device_id = str(segment.get("device_id") or "")
        activity_by_device.setdefault(device_id, []).append(segment)
    for segments in activity_by_device.values():
        segments.sort(key=lambda row: _segment_utc(row, "started"))

    output: dict[str, dict[str, object]] = {}
    search_from: dict[str, int] = {}
    for attention in attention_segments:
        device_id = str(attention.get("device_id") or "")
        activities = activity_by_device.get(device_id, [])
        if not activities:
            continue

        attention_start = _segment_utc(attention, "started")
        attention_end = _segment_utc(attention, "ended")
        if attention_end <= attention_start:
            continue

        index = search_from.get(device_id, 0)
        while (
            index < len(activities)
            and _segment_utc(activities[index], "ended") <= attention_start
        ):
            index += 1
        search_from[device_id] = index

        state = str(attention.get("attention_state") or UNKNOWN)
        if state not in all_states:
            state = UNKNOWN
        weight = float(attention.get("attention_weight") or 0.0)

        for activity in activities[index:]:
            activity_start = _segment_utc(activity, "started")
            if activity_start >= attention_end:
                break
            activity_end = _segment_utc(activity, "ended")
            overlap_start = max(attention_start, activity_start)
            overlap_end = min(attention_end, activity_end)
            seconds = max(0.0, (overlap_end - overlap_start).total_seconds())
            if seconds <= 0:
                continue

            process_name = str(activity.get("process_name") or "(unknown)")
            bucket = output.setdefault(
                process_name,
                {
                    "process_name": process_name,
                    **{state_name: 0.0 for state_name in all_states},
                    "raw_seconds": 0.0,
                    "effective_seconds": 0.0,
                },
            )
            bucket[state] = float(bucket[state]) + seconds
            bucket["raw_seconds"] = float(bucket["raw_seconds"]) + seconds
            bucket["effective_seconds"] = (
                float(bucket["effective_seconds"]) + seconds * weight
            )

    return list(output.values())


def _segment_utc(row: dict[str, object], prefix: str) -> datetime:
    value = row.get(f"{prefix}_at_utc") or row[f"{prefix}_at"]
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _derive_activity_segments(
    rows: list[dict[str, object]],
    segment_gap_seconds: int = DEFAULT_SEGMENT_GAP_SECONDS,
) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    previous_at_utc: datetime | None = None

    for row in rows:
        captured_at = datetime.fromisoformat(str(row["captured_at"]))
        captured_at_utc = _row_utc(row)
        key = _activity_key(row)
        if current is None:
            current = _new_activity_segment(row, captured_at, captured_at_utc)
            previous_at_utc = captured_at_utc
            continue

        assert previous_at_utc is not None
        gap_seconds = max(
            0.0,
            (captured_at_utc - previous_at_utc).total_seconds(),
        )
        current_key = _activity_key(current)
        if key == current_key and gap_seconds <= segment_gap_seconds:
            current["ended_at"] = captured_at.isoformat()
            current["ended_at_utc"] = captured_at_utc.isoformat()
            current["duration_seconds"] = max(
                0.0,
                (
                    captured_at_utc
                    - datetime.fromisoformat(str(current["started_at_utc"]))
                ).total_seconds(),
            )
        else:
            segments.append(current)
            current = _new_activity_segment(row, captured_at, captured_at_utc)
        previous_at_utc = captured_at_utc

    if current is not None:
        segments.append(current)
    return segments


def _activity_key(row: dict[str, object]) -> tuple[object, ...]:
    return (
        row.get("device_id"),
        row["process_name"] or "(unknown)",
        row["window_title"] or "",
        row["executable_path"],
        row["source"],
    )


def _new_activity_segment(
    row: dict[str, object],
    captured_at: datetime,
    captured_at_utc: datetime,
) -> dict[str, object]:
    return {
        "device_id": row.get("device_id"),
        "started_at": captured_at.isoformat(),
        "ended_at": captured_at.isoformat(),
        "started_at_utc": captured_at_utc.isoformat(),
        "ended_at_utc": captured_at_utc.isoformat(),
        "local_date": row.get("local_date"),
        "timezone_id": row.get("timezone_id"),
        "timezone_name": row.get("timezone_name"),
        "utc_offset_seconds": row.get("utc_offset_seconds"),
        "is_dst": row.get("is_dst"),
        "local_day_start_utc": row.get("local_day_start_utc"),
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
    _attach_attention_time_context(output, rows)
    return output


def _attach_attention_time_context(
    segments: list[dict[str, object]],
    rows: list[dict[str, object]],
) -> None:
    if not segments or not rows:
        return
    row_times = [_row_utc(row) for row in rows]
    row_index = 0
    for segment in segments:
        started_at = datetime.fromisoformat(str(segment["started_at"]))
        ended_at = datetime.fromisoformat(str(segment["ended_at"]))
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=timezone.utc)
        started_at_utc = started_at.astimezone(timezone.utc)
        ended_at_utc = ended_at.astimezone(timezone.utc)
        while (
            row_index < len(rows) - 1
            and row_times[row_index] < started_at_utc
        ):
            row_index += 1
        context = rows[row_index]
        segment["device_id"] = context.get("device_id")
        segment["started_at_utc"] = started_at_utc.isoformat()
        segment["ended_at_utc"] = ended_at_utc.isoformat()
        segment["local_date"] = context.get("local_date")
        segment["timezone_id"] = context.get("timezone_id")
        segment["timezone_name"] = context.get("timezone_name")
        segment["utc_offset_seconds"] = context.get("utc_offset_seconds")
        segment["is_dst"] = context.get("is_dst")
        segment["local_day_start_utc"] = context.get("local_day_start_utc")


def _row_utc(row: dict[str, object]) -> datetime:
    value = row.get("captured_at_utc")
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = datetime.fromisoformat(str(row["captured_at"]))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
