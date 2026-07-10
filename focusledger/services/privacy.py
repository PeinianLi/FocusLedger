from __future__ import annotations

from dataclasses import replace
from sqlite3 import Connection

from focusledger.models import ActiveWindowSnapshot


PRIVATE_BROWSER_TITLE = "[private browsing]"

# Substrings that identify a browser process. Matched case-insensitively against
# process_name, so this covers both Windows ("chrome.exe") and macOS
# ("Google Chrome") style names.
_BROWSER_HINTS = (
    "chrome",
    "chromium",
    "msedge",
    "edge",
    "firefox",
    "brave",
    "opera",
    "vivaldi",
    "safari",
)

# Window-title markers that indicate a private / incognito browsing window.
_PRIVATE_TITLE_MARKERS = (
    "incognito",
    "inprivate",
    "private browsing",
    "(private)",
    "private window",
    "\u65e0\u75d5",
    "\u9690\u79c1\u6d4f\u89c8",
    "\u96b1\u79c1\u700f\u89bd",
)

# Window handles that have already been identified as private. Once a browser
# window is recognised as private we keep treating it as private even after its
# title changes to something that no longer carries an obvious marker. This is
# process-level state on purpose: the agent runs as a single long-lived process.
_known_private_windows: set[tuple[int | None, str]] = set()


def _is_browser(process_name: str | None) -> bool:
    if not process_name:
        return False
    name = process_name.lower()
    return any(hint in name for hint in _BROWSER_HINTS)


def _title_marks_private(window_title: str | None) -> bool:
    if not window_title:
        return False
    lowered = window_title.lower()
    return any(marker in lowered for marker in _PRIVATE_TITLE_MARKERS)


def sanitize_snapshot(snapshot: ActiveWindowSnapshot) -> ActiveWindowSnapshot:
    """Redact private/incognito browser window titles before they are stored.

    Non-browser snapshots are returned untouched. For browser windows we treat a
    window as private if its current title carries a private marker, or if its
    window handle was previously identified as private.
    """
    if not _is_browser(snapshot.process_name):
        return snapshot

    handle = snapshot.window_handle
    window_key = (snapshot.process_id, handle) if handle else None
    is_private = _title_marks_private(snapshot.window_title)

    if is_private and window_key:
        _known_private_windows.add(window_key)
    elif window_key and window_key in _known_private_windows:
        is_private = True

    if not is_private:
        return snapshot

    if snapshot.window_title == PRIVATE_BROWSER_TITLE:
        return snapshot
    return replace(snapshot, window_title=PRIVATE_BROWSER_TITLE)


def redact_existing_private_browser_titles(conn: Connection) -> dict[str, int]:
    """Redact private/incognito browser titles already stored in the database.

    Identifies private browser window handles from raw_activity_events (browser
    process + private title marker), then redacts those raw events as well as the
    activity segments that overlap them in time.
    """
    rows = conn.execute(
        "SELECT window_handle, process_name, window_title FROM raw_activity_events"
    ).fetchall()

    private_handles = {
        row["window_handle"]
        for row in rows
        if row["window_handle"]
        and _is_browser(row["process_name"])
        and _title_marks_private(row["window_title"])
    }

    raw_redacted = 0
    segments_redacted = 0
    if not private_handles:
        return {"raw_activity_events": 0, "activity_segments": 0}

    placeholders = ",".join("?" for _ in private_handles)
    handles = list(private_handles)

    raw_ids = [
        row["id"]
        for row in conn.execute(
            f"""
            SELECT id FROM raw_activity_events
            WHERE window_handle IN ({placeholders})
              AND (window_title IS NULL OR window_title != ?)
            """,
            (*handles, PRIVATE_BROWSER_TITLE),
        ).fetchall()
    ]

    segment_ids = []
    if _table_exists(conn, "activity_segments"):
        segment_ids = [
            row["id"]
            for row in conn.execute(
                f"""
                SELECT DISTINCT s.id
                FROM activity_segments s
                JOIN raw_activity_events r
                  ON r.device_id = s.device_id
                 AND r.process_name IS s.process_name
                 AND r.captured_at >= s.started_at
                 AND r.captured_at <= s.ended_at
                WHERE r.window_handle IN ({placeholders})
                  AND (s.window_title IS NULL OR s.window_title != ?)
                """,
                (*handles, PRIVATE_BROWSER_TITLE),
            ).fetchall()
        ]

    for row_id in raw_ids:
        conn.execute(
            "UPDATE raw_activity_events SET window_title = ? WHERE id = ?",
            (PRIVATE_BROWSER_TITLE, row_id),
        )
        raw_redacted += 1

    for row_id in segment_ids:
        conn.execute(
            "UPDATE activity_segments SET window_title = ? WHERE id = ?",
            (PRIVATE_BROWSER_TITLE, row_id),
        )
        segments_redacted += 1

    conn.commit()
    return {
        "raw_activity_events": raw_redacted,
        "activity_segments": segments_redacted,
    }


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None
