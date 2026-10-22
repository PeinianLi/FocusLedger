"""Phase 3: workday sessions.

A workday is the unit a daily review is written about, and it is deliberately
not the same thing as a calendar day: work that runs past midnight still
belongs to the day it started on (its ``workday_key``).

Sessions, pauses and status transitions are user facts and are stored. Anything
measurable from raw samples — how long the day actually ran, which stretches
have no agent coverage — stays derived, so changing a threshold re-explains
history rather than leaving stale rows behind.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from sqlite3 import Connection

from focusledger.agent.heartbeat import heartbeat_stale_after
from focusledger.timeutil import align, parse_aligned
from focusledger.time_context import capture_time_context

NOT_STARTED = "not_started"
ACTIVE = "active"
PAUSED = "paused"
OVERTIME = "overtime"
ENDED = "ended"
HOLIDAY = "holiday"
VACATION = "vacation"

OPEN_STATUSES = (ACTIVE, PAUSED, OVERTIME)
NO_REVIEW_STATUSES = (HOLIDAY, VACATION)

REVIEW_PENDING = "pending"
REVIEW_DONE = "done"


class WorkdayError(RuntimeError):
    """Raised when a requested transition does not apply to the current state."""


@dataclass(frozen=True)
class WorkdayView:
    """A workday plus everything derived about it."""

    session: dict[str, object]
    elapsed_seconds: float
    paused_seconds: float
    worked_seconds: float
    missing_data_seconds: float
    missing_intervals: list[dict[str, object]]
    pauses: list[dict[str, object]]

    @property
    def workday_key(self) -> str:
        return str(self.session["workday_key"])

    @property
    def status(self) -> str:
        return str(self.session["status"])


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def workday_key_for(moment: datetime, config) -> str:
    """Pick the workday a moment belongs to.

    Work started before midnight and continuing after it stays on the earlier
    key. We treat anything before the configured start time as still belonging
    to the previous day, which is what makes a 02:00 finish land on the right
    review.
    """
    start = _parse_clock(config.default_start_time)
    boundary = min(start, time(5, 0))
    if moment.time() < boundary:
        return (moment.date() - timedelta(days=1)).isoformat()
    return moment.date().isoformat()


def _parse_clock(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------
def get_session(conn: Connection, workday_key: str) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT * FROM workday_sessions WHERE workday_key = ?", (workday_key,)
    ).fetchone()
    return dict(row) if row else None


def current_session(conn: Connection) -> dict[str, object] | None:
    """The session still open, if any — regardless of which day it started."""
    row = conn.execute(
        """
        SELECT * FROM workday_sessions
        WHERE ended_at IS NULL AND status IN (?, ?, ?)
        ORDER BY started_at DESC LIMIT 1
        """,
        OPEN_STATUSES,
    ).fetchone()
    return dict(row) if row else None


def list_sessions(conn: Connection, limit: int = 30) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT * FROM workday_sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_pauses(conn: Connection, workday_id: str) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT * FROM workday_pauses WHERE workday_id = ? ORDER BY started_at", (workday_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# transitions
# --------------------------------------------------------------------------
def start_workday(conn: Connection, config, at: datetime | None = None) -> dict[str, object]:
    moment = at or _now()
    key = workday_key_for(moment, config)

    open_session = current_session(conn)
    if open_session:
        raise WorkdayError(
            f"workday {open_session['workday_key']} is still {open_session['status']}; "
            "run `workday offwork` first"
        )
    existing = get_session(conn, key)
    if existing and existing["status"] in NO_REVIEW_STATUSES:
        raise WorkdayError(f"{key} is marked {existing['status']}")
    if existing:
        raise WorkdayError(f"workday {key} already exists (status {existing['status']})")

    session_id = uuid.uuid4().hex
    now_iso = _iso(moment)
    conn.execute(
        """
        INSERT INTO workday_sessions(
            id, device_id, workday_key, started_at, ended_at, timezone, status,
            is_overtime, offwork_target_time, review_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, NULL, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            session_id,
            config.device_id,
            key,
            now_iso,
            capture_time_context(moment).timezone_id,
            ACTIVE,
            config.default_offwork_time, REVIEW_PENDING, now_iso, now_iso,
        ),
    )
    conn.commit()
    return get_session(conn, key)  # type: ignore[return-value]


def pause_workday(conn: Connection, reason: str | None = None, at: datetime | None = None) -> dict[str, object]:
    moment = at or _now()
    session = _require_open(conn)
    if session["status"] == PAUSED:
        raise WorkdayError("workday is already paused")
    conn.execute(
        "INSERT INTO workday_pauses(id, workday_id, started_at, ended_at, reason, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (uuid.uuid4().hex, session["id"], _iso(moment), reason, _iso(moment)),
    )
    _set_status(conn, session["id"], PAUSED, moment)
    conn.commit()
    return get_session(conn, str(session["workday_key"]))  # type: ignore[return-value]


def resume_workday(conn: Connection, at: datetime | None = None) -> dict[str, object]:
    moment = at or _now()
    session = _require_open(conn)
    if session["status"] != PAUSED:
        raise WorkdayError("workday is not paused")
    conn.execute(
        "UPDATE workday_pauses SET ended_at = ? WHERE workday_id = ? AND ended_at IS NULL",
        (_iso(moment), session["id"]),
    )
    restored = OVERTIME if session["is_overtime"] else ACTIVE
    _set_status(conn, session["id"], restored, moment)
    conn.commit()
    return get_session(conn, str(session["workday_key"]))  # type: ignore[return-value]


def mark_overtime(conn: Connection, at: datetime | None = None) -> dict[str, object]:
    moment = at or _now()
    session = _require_open(conn)
    conn.execute(
        "UPDATE workday_sessions SET is_overtime = 1, status = ?, updated_at = ? WHERE id = ?",
        (OVERTIME, _iso(moment), session["id"]),
    )
    conn.commit()
    return get_session(conn, str(session["workday_key"]))  # type: ignore[return-value]


def end_workday(conn: Connection, at: datetime | None = None) -> dict[str, object]:
    moment = at or _now()
    session = _require_open(conn)
    conn.execute(
        "UPDATE workday_pauses SET ended_at = ? WHERE workday_id = ? AND ended_at IS NULL",
        (_iso(moment), session["id"]),
    )
    conn.execute(
        "UPDATE workday_sessions SET ended_at = ?, status = ?, updated_at = ? WHERE id = ?",
        (_iso(moment), ENDED, _iso(moment), session["id"]),
    )
    conn.commit()
    return get_session(conn, str(session["workday_key"]))  # type: ignore[return-value]


def mark_day(conn: Connection, config, workday_key: str, status: str) -> dict[str, object]:
    """Mark a day as holiday or vacation so it never asks for a review."""
    if status not in NO_REVIEW_STATUSES:
        raise WorkdayError(f"unsupported day status: {status}")
    existing = get_session(conn, workday_key)
    now_iso = _iso(_now())
    if existing:
        if existing["status"] in OPEN_STATUSES:
            raise WorkdayError(f"workday {workday_key} is still open; end it first")
        conn.execute(
            "UPDATE workday_sessions SET status = ?, review_status = ?, updated_at = ? WHERE id = ?",
            (status, REVIEW_DONE, now_iso, existing["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO workday_sessions(
                id, device_id, workday_key, started_at, ended_at, timezone, status,
                is_overtime, offwork_target_time, review_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                config.device_id,
                workday_key,
                now_iso,
                now_iso,
                capture_time_context(_now()).timezone_id,
                status,
                REVIEW_DONE,
                now_iso,
                now_iso,
            ),
        )
    conn.commit()
    return get_session(conn, workday_key)  # type: ignore[return-value]


def set_review_status(conn: Connection, workday_key: str, status: str) -> None:
    conn.execute(
        "UPDATE workday_sessions SET review_status = ?, updated_at = ? WHERE workday_key = ?",
        (status, _iso(_now()), workday_key),
    )
    conn.commit()


def _require_open(conn: Connection) -> dict[str, object]:
    session = current_session(conn)
    if not session:
        raise WorkdayError("no workday is running; run `workday start` first")
    return session


def _set_status(conn: Connection, session_id: str, status: str, moment: datetime) -> None:
    conn.execute(
        "UPDATE workday_sessions SET status = ?, updated_at = ? WHERE id = ?",
        (status, _iso(moment), session_id),
    )


# --------------------------------------------------------------------------
# derivation
# --------------------------------------------------------------------------
def describe(conn: Connection, config, workday_key: str, now: datetime | None = None) -> WorkdayView | None:
    """Build the full derived picture of one workday."""
    session = get_session(conn, workday_key)
    if not session:
        return None
    moment = now or _now()

    started = align(datetime.fromisoformat(str(session["started_at"])), moment)
    ended_raw = session["ended_at"]
    ended = align(datetime.fromisoformat(str(ended_raw)), moment) if ended_raw else moment

    # A day left running forever is a data bug, not a 30-hour shift.
    cap = started + timedelta(hours=config.max_workday_hours)
    if ended > cap:
        ended = cap

    pauses = list_pauses(conn, str(session["id"]))
    paused_seconds = 0.0
    for pause in pauses:
        p_start = align(datetime.fromisoformat(str(pause["started_at"])), ended)
        p_end = (
            align(datetime.fromisoformat(str(pause["ended_at"])), ended)
            if pause["ended_at"] else ended
        )
        paused_seconds += max(0.0, (min(p_end, ended) - p_start).total_seconds())

    elapsed = max(0.0, (ended - started).total_seconds())
    missing = missing_data_intervals(conn, config, started, ended)
    missing_seconds = sum(float(m["duration_seconds"]) for m in missing)

    return WorkdayView(
        session=session,
        elapsed_seconds=elapsed,
        paused_seconds=paused_seconds,
        worked_seconds=max(0.0, elapsed - paused_seconds),
        missing_data_seconds=missing_seconds,
        missing_intervals=missing,
        pauses=pauses,
    )


def missing_data_intervals(
    conn: Connection, config, start: datetime, end: datetime
) -> list[dict[str, object]]:
    """Stretches inside a window where the agent was not reporting.

    Derived from heartbeat gaps, so a crashed or stopped agent shows up in the
    review instead of silently looking like idle time. A gap has to exceed the
    heartbeat staleness window before it counts, so the agent's normal
    heartbeat interval and scheduling jitter do not register.
    """
    tolerance = heartbeat_stale_after(config.poll_interval_seconds)
    rows = conn.execute(
        "SELECT timestamp FROM agent_heartbeats WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp",
        (start.isoformat(), end.isoformat()),
    ).fetchall()

    coverage: list[tuple[datetime, datetime]] = []
    for row in rows:
        beat = parse_aligned(row["timestamp"], start)
        if beat is not None:
            coverage.append((beat, beat))

    # Retention removes old heartbeats after preserving the same coverage in
    # cooked activity segments. Include their endpoints so historical reviews
    # do not mistake intentionally archived data for an agent outage.
    cooked_rows = conn.execute(
        """
        SELECT started_at_utc, ended_at_utc, started_at, ended_at
        FROM activity_segments
        WHERE local_date >= ? AND local_date <= ?
        """,
        (
            (start - timedelta(days=1)).date().isoformat(),
            (end + timedelta(days=1)).date().isoformat(),
        ),
    ).fetchall()
    for row in cooked_rows:
        segment_start = parse_aligned(
            row["started_at_utc"] or row["started_at"], start
        )
        segment_end = parse_aligned(
            row["ended_at_utc"] or row["ended_at"], start
        )
        if segment_start is None or segment_end is None:
            continue
        if segment_end < start or segment_start > end:
            continue
        coverage.append(
            (max(start, segment_start), min(end, segment_end))
        )
    coverage.sort(key=lambda interval: interval[0])

    intervals: list[dict[str, object]] = []
    cursor = start
    for covered_start, covered_end in coverage:
        gap = (covered_start - cursor).total_seconds()
        if gap > tolerance:
            intervals.append(_interval(cursor, covered_start))
        cursor = max(cursor, covered_start, covered_end)
    trailing = (end - cursor).total_seconds()
    if trailing > tolerance:
        intervals.append(_interval(cursor, end))
    return intervals


def _interval(start: datetime, end: datetime) -> dict[str, object]:
    return {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_seconds": (end - start).total_seconds(),
        "reason": "agent not running",
    }


def pending_reviews(conn: Connection, before_key: str) -> list[dict[str, object]]:
    """Ended workdays still missing a review, oldest first."""
    rows = conn.execute(
        """
        SELECT * FROM workday_sessions
        WHERE status = ? AND review_status = ? AND workday_key < ?
        ORDER BY workday_key
        """,
        (ENDED, REVIEW_PENDING, before_key),
    ).fetchall()
    return [dict(r) for r in rows]


def day_bounds(conn: Connection, config, workday_key: str) -> tuple[datetime, datetime]:
    """The time window a workday covers, falling back to the calendar day."""
    session = get_session(conn, workday_key)
    day = date.fromisoformat(workday_key)
    if not session:
        start = datetime.combine(day, time(0, 0)).astimezone()
        return start, start + timedelta(days=1)
    started = datetime.fromisoformat(str(session["started_at"]))
    ended_raw = session["ended_at"]
    ended = datetime.fromisoformat(str(ended_raw)) if ended_raw else _now()
    return started, ended
