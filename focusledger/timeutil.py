"""Timestamp helpers shared by the derivation layer.

Rows can carry either aware or naive ISO timestamps: the agent writes aware
ones, but a database restored from an older build, edited by hand, or written
by another tool may not. Subtracting one kind from the other raises TypeError,
which turns a cosmetic data inconsistency into a crashed command.

Everything here normalises rather than rejects, so a mixed database degrades to
slightly imprecise offsets instead of failing.
"""

from __future__ import annotations

from datetime import datetime


def parse_moment(value: object) -> datetime | None:
    """Parse an ISO timestamp, returning None instead of raising."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def align(moment: datetime, reference: datetime) -> datetime:
    """Give ``moment`` the same awareness as ``reference``.

    A naive timestamp is assumed to be in the reference's zone, which is what
    it almost certainly was when written on this machine.
    """
    if reference.tzinfo is None:
        return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment
    if moment.tzinfo is None:
        return moment.replace(tzinfo=reference.tzinfo)
    return moment


def parse_aligned(value: object, reference: datetime) -> datetime | None:
    moment = parse_moment(value)
    return None if moment is None else align(moment, reference)


def overlap_seconds(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    """Seconds two intervals share, tolerating mixed awareness."""
    b_start = align(b_start, a_start)
    b_end = align(b_end, a_start)
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return max(0.0, (earliest_end - latest_start).total_seconds())
