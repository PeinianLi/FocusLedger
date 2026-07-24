"""Phase 7: distraction detection and notifications.

Detection runs inside the agent loop but stays cheap: it tracks a running
category streak in memory and only writes a row when a threshold is crossed.
Nothing here blocks collection — a failed sound or notification is logged and
ignored, never raised into the sampling loop.

Alerts are recorded rather than acted on: the CLI later asks the user *why*, and
that label is what reaches the review. "Distraction" without a reason is a
number that shames the user instead of informing them.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlite3 import Connection

from focusledger.services.classify import ENTERTAINMENT, GAMING

LOGGER = logging.getLogger(__name__)

KIND_GAMING = "gaming"
KIND_ENTERTAINMENT = "entertainment"
KIND_ATTENTION_GAP = "attention_gap"
KIND_PRIVATE_BROWSING = "private_browsing"

REASONS = [
    ("intentional_break", "intentional break"),
    ("procrastination", "procrastination"),
    ("waiting", "waiting for something"),
    ("rabbit_hole", "research rabbit hole"),
    ("emotional", "emotional distraction"),
    ("unclear_task", "unclear task"),
    ("other", "other"),
]
REASON_KEYS = {key for key, _ in REASONS}


@dataclass
class DistractionTracker:
    """Running category streak, held by the agent between samples."""

    gaming_alert_seconds: float
    entertainment_alert_seconds: float
    cooldown_seconds: float
    _category: str | None = None
    _streak_seconds: float = 0.0
    _last_alert_at: dict[str, datetime] = field(default_factory=dict)

    def observe(
        self, category: str, elapsed_seconds: float, now: datetime
    ) -> tuple[str, float] | None:
        """Feed one sample; return ``(kind, streak_seconds)`` when an alert fires."""
        if category != self._category:
            self._category = category
            self._streak_seconds = 0.0
        self._streak_seconds += max(0.0, elapsed_seconds)

        if category == GAMING:
            kind, threshold = KIND_GAMING, self.gaming_alert_seconds
        elif category == ENTERTAINMENT:
            kind, threshold = KIND_ENTERTAINMENT, self.entertainment_alert_seconds
        else:
            return None

        if threshold <= 0 or self._streak_seconds < threshold:
            return None
        last = self._last_alert_at.get(kind)
        if last and (now - last).total_seconds() < self.cooldown_seconds:
            return None
        self._last_alert_at[kind] = now
        return kind, self._streak_seconds

    def reset(self) -> None:
        self._category = None
        self._streak_seconds = 0.0


def record_distraction(
    conn: Connection,
    device_id: str,
    workday_key: str | None,
    kind: str,
    category: str | None,
    process_name: str | None,
    duration_seconds: float,
    detected_at: datetime | None = None,
) -> str:
    moment = (detected_at or datetime.now().astimezone()).isoformat()
    event_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO distraction_events(
            id, device_id, workday_key, detected_at, kind, category, process_name,
            duration_seconds, reason, labeled_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
        """,
        (event_id, device_id, workday_key, moment, kind, category, process_name,
         duration_seconds, moment),
    )
    conn.commit()
    return event_id


def list_distractions(
    conn: Connection, workday_key: str | None = None, unlabeled_only: bool = False
) -> list[dict[str, object]]:
    clauses, params = [], []
    if workday_key:
        clauses.append("workday_key = ?")
        params.append(workday_key)
    if unlabeled_only:
        clauses.append("reason IS NULL")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM distraction_events{where} ORDER BY detected_at", params
    ).fetchall()
    return [dict(r) for r in rows]


def label_distraction(conn: Connection, event_id: str, reason: str) -> bool:
    if reason not in REASON_KEYS:
        raise ValueError(f"unknown reason: {reason}")
    cur = conn.execute(
        "UPDATE distraction_events SET reason = ?, labeled_at = ? WHERE id = ?",
        (reason, datetime.now().astimezone().isoformat(), event_id),
    )
    conn.commit()
    return cur.rowcount > 0


def summarize_reasons(events: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        reason = str(event.get("reason") or "unlabeled")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


# --------------------------------------------------------------------------
# notification side effects — best effort, never fatal
# --------------------------------------------------------------------------
def notify(title: str, message: str, sound: bool = True) -> bool:
    """Show a desktop notification. Returns whether anything was displayed."""
    try:
        if sys.platform == "darwin":
            return _notify_macos(title, message, sound)
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=False, timeout=5)
            return True
        if sys.platform == "win32":
            return _notify_windows(title, message)
    except Exception as exc:  # pragma: no cover - platform dependent
        LOGGER.debug("notification failed: %s", exc)
    return False


def _notify_macos(title: str, message: str, sound: bool) -> bool:
    script = (
        f'display notification {_applescript_string(message)} '
        f'with title {_applescript_string(title)}'
    )
    if sound:
        script += ' sound name "Submarine"'
    result = subprocess.run(
        ["osascript", "-e", script], check=False, capture_output=True, timeout=8
    )
    return result.returncode == 0


def _notify_windows(title: str, message: str) -> bool:  # pragma: no cover - windows only
    script = (
        "[reflection.assembly]::LoadWithPartialName('System.Windows.Forms') > $null; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$n.BalloonTipTitle = {_powershell_string(title)}; "
        f"$n.BalloonTipText = {_powershell_string(message)}; "
        "$n.Visible = $true; $n.ShowBalloonTip(5000)"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False, capture_output=True, timeout=10,
    )
    return result.returncode == 0


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _powershell_string(value: str) -> str:  # pragma: no cover - windows only
    return "'" + value.replace("'", "''") + "'"
