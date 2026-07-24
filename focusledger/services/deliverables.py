"""Phase 6: deliverables, tasks and the daily note.

The point of FocusLedger is not "how long was I busy" but "did the work that
mattered move". Deliverables are the things that matter; time is attributed to
them by matching activity against their keywords.

Attribution is derived, so editing a deliverable's keywords re-attributes past
days too. Only the deliverables, tasks and the user's own written notes are
stored — the machine cannot know what actually progressed, which is exactly why
the note is collected before a review is written.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from sqlite3 import Connection

STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_ARCHIVED = "archived"

TASK_OPEN = "open"
TASK_DONE = "done"
TASK_BLOCKED = "blocked"

PRIORITY_LABELS = {1: "high", 2: "normal", 3: "low"}


def _now() -> str:
    return datetime.now().astimezone().isoformat()


MIN_KEYWORD_LENGTH = 3


def keyword_matches(term: str, haystack: str) -> bool:
    """Whether a deliverable keyword really occurs in a window title.

    Plain substring matching is far too eager: the keyword "a" matches
    "engineering chat", which would attribute an entire day to the wrong
    deliverable. Latin keywords therefore have to land on a word boundary, and
    very short ones are ignored outright. CJK has no word boundaries and is
    matched as a substring, where the false-positive risk is much lower.
    """
    term = (term or "").strip().lower()
    if not term:
        return False
    haystack = (haystack or "").lower()
    if term.isascii():
        if len(term) < MIN_KEYWORD_LENGTH:
            return False
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", haystack) is not None
    return term in haystack


def usable_keywords(deliverable: dict[str, object]) -> list[str]:
    raw = str(deliverable.get("match_keywords") or "")
    terms = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if not terms:
        terms = [str(deliverable.get("title", "")).strip().lower()]
    return [t for t in terms if t and (not t.isascii() or len(t) >= MIN_KEYWORD_LENGTH)]


# --------------------------------------------------------------------------
# deliverables
# --------------------------------------------------------------------------
def add_deliverable(
    conn: Connection,
    title: str,
    priority: int = 2,
    due_date: str | None = None,
    keywords: str | None = None,
) -> dict[str, object]:
    now = _now()
    deliverable_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO deliverables(id, title, status, priority, due_date, match_keywords,
                                 created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (deliverable_id, title.strip(), STATUS_ACTIVE, priority, due_date, keywords, now, now),
    )
    conn.commit()
    return get_deliverable(conn, deliverable_id)  # type: ignore[return-value]


def get_deliverable(conn: Connection, deliverable_id: str) -> dict[str, object] | None:
    row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    return dict(row) if row else None


def find_deliverable(conn: Connection, needle: str) -> dict[str, object] | None:
    """Resolve by id prefix or by (case-insensitive) title fragment."""
    row = conn.execute(
        "SELECT * FROM deliverables WHERE id = ? OR id LIKE ? LIMIT 1", (needle, needle + "%")
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "SELECT * FROM deliverables WHERE lower(title) LIKE ? ORDER BY created_at LIMIT 1",
        (f"%{needle.lower()}%",),
    ).fetchone()
    return dict(row) if row else None


def list_deliverables(conn: Connection, include_archived: bool = False) -> list[dict[str, object]]:
    query = "SELECT * FROM deliverables"
    if not include_archived:
        query += f" WHERE status != '{STATUS_ARCHIVED}'"
    query += " ORDER BY status, priority, created_at"
    return [dict(r) for r in conn.execute(query).fetchall()]


def update_deliverable(conn: Connection, deliverable_id: str, **fields) -> dict[str, object] | None:
    allowed = {"title", "status", "priority", "due_date", "match_keywords"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_deliverable(conn, deliverable_id)
    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE deliverables SET {assignments}, updated_at = ? WHERE id = ?",
        (*updates.values(), _now(), deliverable_id),
    )
    conn.commit()
    return get_deliverable(conn, deliverable_id)


# --------------------------------------------------------------------------
# tasks
# --------------------------------------------------------------------------
def add_task(
    conn: Connection,
    title: str,
    deliverable_id: str | None = None,
    estimate_minutes: int | None = None,
) -> dict[str, object]:
    now = _now()
    task_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO tasks(id, deliverable_id, title, status, estimate_minutes, blocker,
                          created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (task_id, deliverable_id, title.strip(), TASK_OPEN, estimate_minutes, now, now),
    )
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def get_task(conn: Connection, task_id: str) -> dict[str, object] | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def find_task(conn: Connection, needle: str) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ? OR id LIKE ? LIMIT 1", (needle, needle + "%")
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "SELECT * FROM tasks WHERE lower(title) LIKE ? ORDER BY created_at LIMIT 1",
        (f"%{needle.lower()}%",),
    ).fetchone()
    return dict(row) if row else None


def list_tasks(conn: Connection, deliverable_id: str | None = None,
               include_done: bool = True) -> list[dict[str, object]]:
    clauses, params = [], []
    if deliverable_id:
        clauses.append("deliverable_id = ?")
        params.append(deliverable_id)
    if not include_done:
        clauses.append("status != 'done'")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"SELECT * FROM tasks{where} ORDER BY status, created_at", params).fetchall()
    return [dict(r) for r in rows]


def set_task_status(conn: Connection, task_id: str, status: str,
                    blocker: str | None = None) -> dict[str, object] | None:
    conn.execute(
        "UPDATE tasks SET status = ?, blocker = ?, updated_at = ? WHERE id = ?",
        (status, blocker, _now(), task_id),
    )
    conn.commit()
    return get_task(conn, task_id)


# --------------------------------------------------------------------------
# daily note
# --------------------------------------------------------------------------
def save_daily_note(
    conn: Connection, workday_key: str, progress: str, blocked: str, tomorrow: str
) -> dict[str, object]:
    now = _now()
    conn.execute(
        """
        INSERT INTO daily_notes(id, workday_key, progress, blocked, tomorrow, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workday_key) DO UPDATE SET
            progress = excluded.progress,
            blocked = excluded.blocked,
            tomorrow = excluded.tomorrow,
            updated_at = excluded.updated_at
        """,
        (uuid.uuid4().hex, workday_key, progress, blocked, tomorrow, now, now),
    )
    conn.commit()
    return get_daily_note(conn, workday_key)  # type: ignore[return-value]


def get_daily_note(conn: Connection, workday_key: str) -> dict[str, object] | None:
    row = conn.execute("SELECT * FROM daily_notes WHERE workday_key = ?", (workday_key,)).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# time attribution (derived)
# --------------------------------------------------------------------------
def attribute_time(
    conn: Connection, classified_segments: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Split effective time across deliverables by keyword match.

    A segment is credited to a deliverable when the classifier already linked
    it, or when the deliverable's keywords appear in the process name or title.
    Segments matching nothing are simply not attributed — inventing a link
    would make the progress numbers lie.
    """
    deliverables = list_deliverables(conn)
    if not deliverables:
        return []

    totals: dict[str, dict[str, object]] = {
        str(d["id"]): {
            "id": d["id"], "title": d["title"], "status": d["status"],
            "priority": d["priority"], "due_date": d["due_date"],
            "seconds": 0.0, "effective_seconds": 0.0, "segments": 0,
        }
        for d in deliverables
    }

    keyword_index = [
        (str(deliverable["id"]), usable_keywords(deliverable)) for deliverable in deliverables
    ]

    for segment in classified_segments:
        duration = float(segment.get("duration_seconds") or 0.0)
        if duration <= 0:
            continue
        target = segment.get("deliverable_id")
        if not target:
            haystack = f"{segment.get('process', '')} {segment.get('title', '')}".lower()
            for deliverable_id, terms in keyword_index:
                if any(keyword_matches(term, haystack) for term in terms):
                    target = deliverable_id
                    break
        if not target or target not in totals:
            continue
        entry = totals[str(target)]
        entry["seconds"] = float(entry["seconds"]) + duration
        entry["effective_seconds"] = float(entry["effective_seconds"]) + duration * float(
            segment.get("attention_weight", 1.0)
        )
        entry["segments"] = int(entry["segments"]) + 1

    return sorted(
        (e for e in totals.values() if float(e["seconds"]) > 0),
        key=lambda e: float(e["seconds"]),
        reverse=True,
    )


def stalled_deliverables(
    conn: Connection, attribution: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Active deliverables that got no time today — what the review must flag."""
    touched = {str(entry["id"]) for entry in attribution}
    return [
        d for d in list_deliverables(conn)
        if d["status"] == STATUS_ACTIVE and str(d["id"]) not in touched
    ]
