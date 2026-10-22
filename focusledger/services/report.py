from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from sqlite3 import Connection

from focusledger.services.activity import (
    day_segments,
    summarize_attention_segments,
    summarize_segments_by_app,
)
from focusledger.services.alerts import list_distractions
from focusledger.services.attention import ATTENTION_STATES, UNKNOWN, AttentionPolicy
from focusledger.services.classify import (
    CATEGORY_LABELS,
    attach_attention_weight,
    classify_segments,
    load_rule_context,
    summarize_categories,
)
from focusledger.services.deliverables import attribute_time, list_tasks, stalled_deliverables
from focusledger.services import review as review_service
from focusledger.services import workday as workday_service


def build_report_data(
    conn: Connection,
    day: date,
    poll_interval_seconds: int,
    policy: AttentionPolicy | None = None,
    config: object | None = None,
) -> dict[str, object]:
    """Aggregate one local day of raw samples into a JSON-serialisable report.

    Recent dates are derived from raw events; archived dates are read from the
    cooked activity and attention segment tables. The HTML report is a pure
    view over this dict.
    """
    policy = policy or AttentionPolicy()
    policy_config = config

    # A recent day is derived from tens of thousands of raw samples; read and
    # derive them once and build every view from the same segments.
    activity_segments, attention_segments = day_segments(conn, day, policy)
    app_rows = summarize_segments_by_app(activity_segments)
    attention_summary = summarize_attention_segments(activity_segments, attention_segments)

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

    classified = classify_segments(activity_segments, load_rule_context(conn))
    attach_attention_weight(classified, attention_segments)

    return {
        "meta": {
            "date": day.isoformat(),
            "poll_interval_seconds": poll_interval_seconds,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "workday": _workday_block(conn, day, policy_config),
        "categories": {
            "totals": summarize_categories(classified),
            "labels": dict(CATEGORY_LABELS),
            "segments": [
                {
                    "start_min": _timeline_minute(
                        row,
                        "started",
                        datetime.fromisoformat(str(row["started_at"])),
                    ),
                    "end_min": _timeline_minute(
                        row,
                        "ended",
                        datetime.fromisoformat(str(row["ended_at"])),
                    ),
                    "category": str(row["category"]),
                    "matched_by": str(row["matched_by"]),
                    "site": row.get("site"),
                    "duration_seconds": float(row.get("duration_seconds") or 0.0),
                }
                for row in classified
            ],
        },
        "deliverables": _deliverable_block(conn, classified),
        "distractions": _distraction_block(conn, day),
        "review": _review_block(conn, day),
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


def _workday_block(conn: Connection, day: date, config) -> dict[str, object] | None:
    """The workday session covering this date, with derived totals."""
    key = day.isoformat()
    session = workday_service.get_session(conn, key)
    if not session:
        return None
    view = workday_service.describe(conn, config, key) if config is not None else None
    block: dict[str, object] = {
        "workday_key": key,
        "status": str(session["status"]),
        "started_at": str(session["started_at"]),
        "ended_at": session["ended_at"],
        "is_overtime": bool(session["is_overtime"]),
        "review_status": str(session["review_status"]),
        "start_min": _minutes_since_midnight(datetime.fromisoformat(str(session["started_at"]))),
        "end_min": (
            _minutes_since_midnight(datetime.fromisoformat(str(session["ended_at"])))
            if session["ended_at"] else None
        ),
    }
    if view:
        block.update({
            "worked_seconds": view.worked_seconds,
            "paused_seconds": view.paused_seconds,
            "missing_data_seconds": view.missing_data_seconds,
            "missing_intervals": [
                {
                    "start_min": _minutes_since_midnight(
                        datetime.fromisoformat(str(i["started_at"]))
                    ),
                    "end_min": _minutes_since_midnight(
                        datetime.fromisoformat(str(i["ended_at"]))
                    ),
                    "duration_seconds": float(i["duration_seconds"]),
                    "reason": str(i["reason"]),
                }
                for i in view.missing_intervals
            ],
        })
    return block


def _deliverable_block(conn: Connection, classified: list[dict[str, object]]) -> dict[str, object]:
    attribution = attribute_time(conn, classified)
    return {
        "attributed": [
            {
                "id": str(entry["id"]),
                "title": str(entry["title"]),
                "status": str(entry["status"]),
                "priority": int(entry["priority"]),
                "due_date": entry["due_date"],
                "seconds": float(entry["seconds"]),
                "effective_seconds": float(entry["effective_seconds"]),
            }
            for entry in attribution
        ],
        "stalled": [
            {"id": str(d["id"]), "title": str(d["title"]), "due_date": d["due_date"]}
            for d in stalled_deliverables(conn, attribution)
        ],
        "open_tasks": [
            {
                "id": str(t["id"]),
                "title": str(t["title"]),
                "status": str(t["status"]),
                "estimate_minutes": t["estimate_minutes"],
                "blocker": t["blocker"],
            }
            for t in list_tasks(conn, include_done=False)
        ],
    }


def _distraction_block(conn: Connection, day: date) -> list[dict[str, object]]:
    events = list_distractions(conn, day.isoformat())
    out = []
    for event in events:
        try:
            detected = datetime.fromisoformat(str(event["detected_at"]))
        except ValueError:
            continue
        out.append({
            "id": str(event["id"]),
            "at_min": _minutes_since_midnight(detected),
            "kind": str(event["kind"]),
            "category": event["category"],
            "process": event["process_name"],
            "duration_seconds": float(event["duration_seconds"] or 0.0),
            "reason": event["reason"],
        })
    return out


def _review_block(conn: Connection, day: date) -> dict[str, object] | None:
    saved = review_service.get_review(conn, day.isoformat())
    if not saved:
        return None
    return {
        "generator": str(saved["generator"]),
        "llm_status": str(saved["llm_status"]),
        "updated_at": str(saved["updated_at"]),
        "markdown": str(saved["markdown"]),
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
        "start_min": _timeline_minute(row, "started", started_at),
        "end_min": _timeline_minute(row, "ended", ended_at),
        "start_label": _time_label(started_at, row),
        "end_label": _time_label(ended_at, row),
        "state": str(row["attention_state"]),
        "weight": float(row["attention_weight"] or 0.0),
        "duration_seconds": float(row["duration_seconds"] or 0.0),
        "max_idle_seconds": (
            None if row.get("max_idle_seconds") is None else float(row["max_idle_seconds"])
        ),
        "annotation": row.get("annotation"),
        **_timezone_fields(row),
    }


def _activity_segment(row: dict[str, object]) -> dict[str, object]:
    started_at = datetime.fromisoformat(str(row["started_at"]))
    ended_at = datetime.fromisoformat(str(row["ended_at"]))
    return {
        "start_min": _timeline_minute(row, "started", started_at),
        "end_min": _timeline_minute(row, "ended", ended_at),
        "start_label": _time_label(started_at, row),
        "end_label": _time_label(ended_at, row),
        "process": str(row["process_name"] or "(unknown)"),
        "title": str(row["window_title"] or ""),
        "duration_seconds": float(row["duration_seconds"] or 0.0),
        **_timezone_fields(row),
    }


def _timeline_minute(
    row: dict[str, object],
    prefix: str,
    fallback: datetime,
) -> float:
    moment_utc = row.get(f"{prefix}_at_utc")
    day_start_utc = row.get("local_day_start_utc")
    if moment_utc and day_start_utc:
        moment = datetime.fromisoformat(
            str(moment_utc).replace("Z", "+00:00")
        )
        day_start = datetime.fromisoformat(
            str(day_start_utc).replace("Z", "+00:00")
        )
        return (moment - day_start).total_seconds() / 60
    return _minutes_since_midnight(fallback)


def _time_label(moment: datetime, row: dict[str, object]) -> str:
    timezone_name = row.get("timezone_name")
    if timezone_name:
        return f"{moment:%H:%M:%S} {timezone_name}"
    offset = moment.strftime("%z")
    if offset:
        offset = f"{offset[:3]}:{offset[3:]}"
        return f"{moment:%H:%M:%S} UTC{offset}"
    return moment.strftime("%H:%M:%S")


def _timezone_fields(row: dict[str, object]) -> dict[str, object]:
    return {
        "timezone_id": row.get("timezone_id"),
        "timezone_name": row.get("timezone_name"),
        "utc_offset_seconds": row.get("utc_offset_seconds"),
        "is_dst": (
            None if row.get("is_dst") is None else bool(row["is_dst"])
        ),
    }


def _minutes_since_midnight(moment: datetime) -> float:
    """Wall-clock minutes from local midnight.

    Uses the timestamp's own clock fields rather than an epoch conversion, so the
    report shows the hour the sample was captured at regardless of the viewer's
    timezone.
    """
    return moment.hour * 60 + moment.minute + moment.second / 60 + moment.microsecond / 6e7


def render_report_html(data: dict[str, object]) -> str:
    """Inline the live UI and a data snapshot into one standalone HTML file.

    The exported file runs exactly the same React app the server serves; the app
    switches to snapshot mode when it finds the inlined data element instead of
    fetching from the API.
    """
    static_root = Path(__file__).resolve().parent.parent / "server" / "static"
    css = (static_root / "app.css").read_text(encoding="utf-8")
    app_js = (static_root / "app.js").read_text(encoding="utf-8")
    vendor = "\n".join(
        (static_root / "vendor" / name).read_text(encoding="utf-8")
        for name in ("react.production.min.js", "react-dom.production.min.js", "htm.umd.js")
    )

    payload = json.dumps(data, ensure_ascii=False)
    # Escape '<' so a window title containing "</script>" or "<!--" cannot break
    # out of the JSON <script> tag. JSON.parse reads < back as '<'.
    payload = payload.replace("<", "\\u003c")

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>FocusLedger — Attention Ledger</title>\n"
        f"<style>\n{css}\n</style>\n</head>\n<body>\n"
        "<div id=\"root\"></div>\n"
        f"<script>\n{vendor}\n</script>\n"
        f"<script id=\"focusledger-data\" type=\"application/json\">{payload}</script>\n"
        f"<script>\n{app_js}\n</script>\n"
        "</body>\n</html>\n"
    )
