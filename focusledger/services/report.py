from __future__ import annotations

import json
from datetime import date, datetime, timezone
from importlib.resources import files
from sqlite3 import Connection

from focusledger.services.activity import (
    list_attention_segments,
    list_timeline,
    summarize_attention,
    summarize_by_app,
)
from focusledger.services.attention import ATTENTION_STATES, UNKNOWN, AttentionPolicy


DATA_TOKEN = "__FOCUSLEDGER_REPORT_DATA__"


def build_report_data(
    conn: Connection,
    day: date,
    poll_interval_seconds: int,
    policy: AttentionPolicy | None = None,
) -> dict[str, object]:
    """Aggregate one local day of raw samples into a JSON-serialisable report.

    Everything here is derived at query time from raw events, matching the rest
    of FocusLedger: nothing is read from a stored derived table. The HTML report
    is a pure view over this dict.
    """
    policy = policy or AttentionPolicy()

    attention_segments = list_attention_segments(conn, day, policy)
    activity_segments = list_timeline(conn, day)
    app_rows = summarize_by_app(conn, day)
    attention_summary = summarize_attention(conn, day, poll_interval_seconds, policy)

    all_states = (*ATTENTION_STATES, UNKNOWN)
    totals = {
        state: float(attention_summary.get(f"{state}_seconds", 0.0)) for state in all_states
    }
    weights = {
        "active": policy.active_weight,
        "low_activity": policy.low_activity_weight,
        "attention_gap": policy.attention_gap_weight,
        "interrupted": policy.interrupted_weight,
        "hard_idle": policy.hard_idle_weight,
        "unknown": 0.0,
    }

    return {
        "meta": {
            "date": day.isoformat(),
            "poll_interval_seconds": poll_interval_seconds,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "attention": {
            "totals": totals,
            "raw_seconds": float(attention_summary.get("raw_seconds", 0.0)),
            "effective_seconds": float(attention_summary.get("effective_seconds", 0.0)),
            "weights": weights,
            "segments": [_attention_segment(row) for row in attention_segments],
        },
        "activity": {
            "segments": [_activity_segment(row) for row in activity_segments],
            "apps": [
                {
                    "process": str(row["process_name"]),
                    "total_seconds": float(row["total_seconds"] or 0.0),
                    "segment_count": int(row["segment_count"] or 0),
                }
                for row in app_rows
            ],
        },
        "attention_per_app": [
            {
                "process": str(row["process"]),
                "raw_seconds": float(row.get("raw_seconds", 0.0)),
                "effective_seconds": float(row.get("effective_seconds", 0.0)),
                **{state: float(row.get(state, 0.0)) for state in all_states},
            }
            for row in _per_app_rows(attention_summary)
        ],
    }


def _per_app_rows(attention_summary: dict[str, object]) -> list[dict[str, object]]:
    rows = attention_summary.get("per_app", [])
    normalized: list[dict[str, object]] = []
    for row in rows:
        row = dict(row)
        row["process"] = row.get("process_name", row.get("process", "(unknown)"))
        normalized.append(row)
    return normalized


def _attention_segment(row: dict[str, object]) -> dict[str, object]:
    started_at = datetime.fromisoformat(str(row["started_at"]))
    ended_at = datetime.fromisoformat(str(row["ended_at"]))
    return {
        "start_min": _minutes_since_midnight(started_at),
        "end_min": _minutes_since_midnight(ended_at),
        "start_label": started_at.strftime("%H:%M:%S"),
        "end_label": ended_at.strftime("%H:%M:%S"),
        "state": str(row["attention_state"]),
        "weight": float(row["attention_weight"] or 0.0),
        "duration_seconds": float(row["duration_seconds"] or 0.0),
        "max_idle_seconds": (
            None if row.get("max_idle_seconds") is None else float(row["max_idle_seconds"])
        ),
    }


def _activity_segment(row: dict[str, object]) -> dict[str, object]:
    started_at = datetime.fromisoformat(str(row["started_at"]))
    ended_at = datetime.fromisoformat(str(row["ended_at"]))
    return {
        "start_min": _minutes_since_midnight(started_at),
        "end_min": _minutes_since_midnight(ended_at),
        "start_label": started_at.strftime("%H:%M:%S"),
        "end_label": ended_at.strftime("%H:%M:%S"),
        "process": str(row["process_name"] or "(unknown)"),
        "title": str(row["window_title"] or ""),
        "duration_seconds": float(row["duration_seconds"] or 0.0),
    }


def _minutes_since_midnight(moment: datetime) -> float:
    """Wall-clock minutes from local midnight.

    Uses the timestamp's own clock fields rather than an epoch conversion, so the
    report shows the hour the sample was captured at regardless of the viewer's
    timezone.
    """
    return moment.hour * 60 + moment.minute + moment.second / 60 + moment.microsecond / 6e7


def render_report_html(data: dict[str, object]) -> str:
    template = files("focusledger.report").joinpath("template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False)
    # Escape '<' so a window title containing "</script>" or "<!--" cannot break
    # out of the JSON <script> tag. JSON.parse reads < back as '<'.
    payload = payload.replace("<", "\\u003c")
    if DATA_TOKEN not in template:
        raise ValueError("report template is missing the data injection token")
    return template.replace(DATA_TOKEN, payload)
