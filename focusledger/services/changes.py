"""Changes written to the database on the user's behalf.

The analyst can only *propose* a change. A proposal carries the exact rows it
would write and a preview of their effect; nothing touches the database until
the user approves it. Every applied change is logged with what it takes to
undo it, and raw samples are never rewritten: an absence is an annotation read
over the data, and a category change is a correction like one made by hand.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlite3 import Connection

from focusledger.services.classify import CATEGORIES, SCOPES

# Labels for time the user was not at the computer. They all read the same way
# (attention 0, category idle); the label records why.
ABSENCE_LABELS = ("sleep", "away", "offline", "meal", "break")


class ChangeError(ValueError):
    """A change that cannot be applied or undone as asked."""


@dataclass
class Proposal:
    action: str
    arguments: dict[str, object]
    summary: str
    preview: dict[str, object]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_json_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "action": self.action,
            "arguments": self.arguments,
            "summary": self.summary,
            "preview": self.preview,
        }


def apply(conn: Connection, config, proposal: Proposal, source: str = "analyst") -> dict[str, object]:
    """Write an approved proposal and log it, in one transaction."""
    change_id = uuid.uuid4().hex[:12]
    now = _now()
    args = proposal.arguments
    conn.execute("BEGIN IMMEDIATE")
    try:
        if proposal.action == "mark_absent":
            label = str(args["label"])
            if label not in ABSENCE_LABELS:
                raise ChangeError(f"unknown absence label: {label}")
            for start, end in args["ranges"]:  # type: ignore[union-attr]
                conn.execute(
                    """
                    INSERT INTO time_annotations(
                        id, local_date, started_at_utc, ended_at_utc, label, note,
                        change_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (uuid.uuid4().hex, args["date"], start, end, label, args.get("note"),
                     change_id, now),
                )
            undo: dict[str, object] = {"delete_annotations_of": change_id}
        elif proposal.action == "correct_category":
            scope, pattern, category = str(args["scope"]), str(args["pattern"]), str(args["category"])
            if scope not in SCOPES or category not in CATEGORIES:
                raise ChangeError(f"invalid correction: {scope} {pattern} -> {category}")
            previous = conn.execute(
                "SELECT category FROM manual_corrections WHERE scope = ? AND pattern = ?",
                (scope, pattern),
            ).fetchone()
            _set_correction(conn, config, scope, pattern, category, now)
            undo = {"restore_correction": {
                "scope": scope, "pattern": pattern,
                "category": previous[0] if previous else None,
            }}
        else:
            raise ChangeError(f"unknown action: {proposal.action}")
        conn.execute(
            """
            INSERT INTO change_log(id, created_at, source, action, arguments, summary, undo)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (change_id, now, source, proposal.action,
             json.dumps(args, ensure_ascii=False), proposal.summary, json.dumps(undo)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_change(conn, change_id)  # type: ignore[return-value]


def undo(conn: Connection, config, change_id: str) -> dict[str, object]:
    row = conn.execute("SELECT undo, undone_at FROM change_log WHERE id = ?", (change_id,)).fetchone()
    if row is None:
        raise ChangeError(f"no change {change_id}")
    if row[1]:
        raise ChangeError(f"change {change_id} was already undone")
    plan = json.loads(row[0])
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "delete_annotations_of" in plan:
            conn.execute("DELETE FROM time_annotations WHERE change_id = ?", (change_id,))
        if "restore_correction" in plan:
            restore = plan["restore_correction"]
            if restore["category"] is None:
                conn.execute(
                    "DELETE FROM manual_corrections WHERE scope = ? AND pattern = ?",
                    (restore["scope"], restore["pattern"]),
                )
            else:
                _set_correction(conn, config, restore["scope"], restore["pattern"],
                                restore["category"], now)
        conn.execute("UPDATE change_log SET undone_at = ? WHERE id = ?", (now, change_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_change(conn, change_id)  # type: ignore[return-value]


def get_change(conn: Connection, change_id: str) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT id, created_at, source, action, arguments, summary, undone_at "
        "FROM change_log WHERE id = ?",
        (change_id,),
    ).fetchone()
    return _change_dict(row) if row else None


def list_changes(conn: Connection, limit: int = 20) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT id, created_at, source, action, arguments, summary, undone_at "
        "FROM change_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_change_dict(row) for row in rows]


def _set_correction(conn: Connection, config, scope: str, pattern: str, category: str,
                    now: str) -> None:
    conn.execute(
        """
        INSERT INTO manual_corrections(id, device_id, scope, pattern, category, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, pattern) DO UPDATE SET
            category = excluded.category, created_at = excluded.created_at
        """,
        (uuid.uuid4().hex, getattr(config, "device_id", "unknown"), scope, pattern, category, now),
    )


def _change_dict(row) -> dict[str, object]:
    return {
        "id": row[0],
        "created_at": row[1],
        "source": row[2],
        "action": row[3],
        "arguments": json.loads(row[4]),
        "summary": row[5],
        "undone_at": row[6],
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
