"""LLM analyst: a tool-calling harness over FocusLedger's own data.

A local model answers questions about tracked time by calling read-only tools.
The tools do every calculation in Python and return finished figures, so the
model's job stays what it is everywhere else in FocusLedger: decide what to
look at and say it well, never compute. A grounding check then flags any
number in the answer that no tool returned.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from datetime import time as day_time
from sqlite3 import Connection
from typing import Callable

from focusledger.services import llm as llm_service
from focusledger.services import workday as workday_service
from focusledger.services.activity import absences_between, day_segments
from focusledger.services.alerts import list_distractions
from focusledger.services.attention import AttentionPolicy
from focusledger.services.changes import ABSENCE_LABELS, Proposal
from focusledger.services.classify import (
    CATEGORIES,
    SCOPES,
    UNKNOWN,
    attach_attention_weight,
    classify_segments,
    load_rule_context,
)
from focusledger.services.deliverables import attribute_time, list_deliverables, list_tasks

UNKNOWN_APP = "(unknown)"
FOCUS_BLOCK_MINUTES = 25
MAX_RANGE_DAYS = 92
DEFAULT_ROUNDS = 8
DEFAULT_NUM_CTX = 16384
# How often an answer is sent back: for skipping the tools, or for numbers no
# tool returned. Small models do both; one pointed retry usually fixes it.
MAX_CORRECTIONS = 2
MAX_HISTORY_TURNS = 4

_NO_TOOLS_CORRECTION = (
    "You answered without calling any tool, so none of that comes from the user's "
    "data. Call the tools you need (start with list_days), then answer again."
)
_GROUNDING_CORRECTION = (
    "These numbers are not in any tool result: {numbers}. Rewrite the answer so "
    "every number is copied from a tool result in the same unit. If you need a "
    "figure you do not have, call a tool for it. Do not compute."
)


class ToolError(ValueError):
    """Bad tool arguments; the message goes back to the model so it can retry."""


# --------------------------------------------------------------------------
# per-run data access
# --------------------------------------------------------------------------
@dataclass
class DayFacts:
    date: str
    activity: list[dict[str, object]]
    attention: list[dict[str, object]]

    @property
    def recorded_seconds(self) -> float:
        return sum(_seconds(s) for s in self.activity)

    @property
    def attention_tracked(self) -> bool:
        return any(s.get("attention_state") != UNKNOWN for s in self.attention)

    @property
    def effective_seconds(self) -> float:
        return sum(_seconds(s) * float(s.get("attention_weight") or 0.0) for s in self.attention)

    def state_seconds(self) -> Counter:
        totals: Counter = Counter()
        for segment in self.attention:
            totals[str(segment.get("attention_state"))] += _seconds(segment)
        return totals

    def totals_by(self, key: str) -> dict[str, list[float]]:
        """``{name: [foreground_seconds, effective_seconds]}`` grouped by ``key``."""
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for segment in self.activity:
            name = segment.get(key)
            if not name:
                continue
            seconds = _seconds(segment)
            totals[str(name)][0] += seconds
            totals[str(name)][1] += seconds * float(segment.get("attention_weight") or 0.0)
        return totals


def tracked_dates(conn: Connection) -> list[str]:
    """Every local date with recorded activity, raw or archived, oldest first."""
    rows = conn.execute(
        """
        SELECT local_date FROM activity_segments
        UNION
        SELECT local_date FROM raw_activity_events
        ORDER BY 1
        """
    ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


class Ledger:
    """Tracked data for one analysis run; each day is computed at most once."""

    def __init__(self, conn: Connection, config) -> None:
        self.conn = conn
        self.config = config
        self.policy = AttentionPolicy.from_config(config)
        self._rules = load_rule_context(conn)
        self._days: dict[str, DayFacts] = {}
        self._dates: list[str] | None = None
        # Changes the model proposed in this run; none is written until approved.
        self.proposals: list[Proposal] = []

    def tracked_dates(self) -> list[str]:
        if self._dates is None:
            self._dates = tracked_dates(self.conn)
        return self._dates

    def dates_between(self, start: str, end: str) -> list[str]:
        return [d for d in self.tracked_dates() if start <= d <= end]

    def day(self, day: str) -> DayFacts:
        if day not in self._days:
            activity, attention = day_segments(self.conn, date.fromisoformat(day), self.policy)
            classified = classify_segments(activity, self._rules)
            attach_attention_weight(classified, attention)
            self._days[day] = DayFacts(day, classified, attention)
        return self._days[day]

    def attention_tracked_from(self) -> str | None:
        row = self.conn.execute(
            """
            SELECT MIN(d) FROM (
                SELECT MIN(local_date) AS d FROM attention_segments
                WHERE attention_state != 'unknown'
                UNION ALL
                SELECT MIN(local_date) FROM raw_activity_events
                WHERE idle_seconds IS NOT NULL
            )
            """
        ).fetchone()
        return str(row[0]) if row and row[0] else None


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, object]
    handler: Callable[[Ledger, dict[str, object]], dict[str, object]]

    def schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _params(required: tuple[str, ...] = (), **properties: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": text} for name, text in properties.items()
        },
        "required": list(required),
    }


def tool_list_days(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    dates = ledger.tracked_dates()
    if not dates:
        return {"tracked_days": 0, "note": "Nothing has been recorded yet."}
    raw_dates = {
        str(row[0])
        for row in ledger.conn.execute("SELECT DISTINCT local_date FROM raw_activity_events")
    }
    recent = dates[-7:]
    previous = dates[-14:-7]
    return {
        "first_date": dates[0],
        "last_date": dates[-1],
        "tracked_days": len(dates),
        "untracked_ranges": _gaps(dates),
        "attention_tracked_from": ledger.attention_tracked_from(),
        "last_7_tracked_days": {"start": recent[0], "end": recent[-1]},
        "previous_7_tracked_days": (
            {"start": previous[0], "end": previous[-1]} if previous else None
        ),
        "raw_dates": sorted(raw_dates),
    }


def tool_day_summary(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    day = _tracked_date(ledger, args, "date")
    facts = ledger.day(day)
    recorded = facts.recorded_seconds
    apps = facts.totals_by("process_name")
    categories = facts.totals_by("category")
    app_category = _dominant(facts.activity, "process_name", "category")
    states = facts.state_seconds()

    result: dict[str, object] = {
        "date": day,
        "attention_tracked": facts.attention_tracked,
        "recorded_hours": _h(recorded),
        "effective_hours": _h(facts.effective_seconds) if facts.attention_tracked else None,
        # Effective time follows the attention timeline, which also covers the
        # seconds between app samples, so on an all-active day it can edge past
        # recorded time; a share over 100% only confuses the model.
        "effective_share_of_recorded_pct": (
            min(100.0, _pct(facts.effective_seconds, recorded))
            if facts.attention_tracked else None
        ),
        "attention_state_hours": {k: _h(v) for k, v in states.most_common() if v >= 60},
        "locked_or_system_hours": _h(apps.get(UNKNOWN_APP, [0.0])[0]),
        "categories": [
            {
                "category": name,
                "hours": _h(fg),
                "effective_hours": _h(eff),
                "share_of_recorded_pct": _pct(fg, recorded),
            }
            for name, (fg, eff) in _top(categories, 9)
        ],
        "top_apps": [
            {
                "app": name,
                "category": app_category.get(name),
                "hours": _h(fg),
                "effective_hours": _h(eff),
            }
            for name, (fg, eff) in _top(apps, 8, skip=UNKNOWN_APP)
        ],
        "top_sites": [
            {"site": name, "hours": _h(fg), "effective_hours": _h(eff)}
            for name, (fg, eff) in _top(facts.totals_by("site"), 5)
        ],
        "focus_blocks": _focus_blocks(facts),
    }
    used = [s for s in facts.activity if s.get("process_name") != UNKNOWN_APP and _seconds(s) > 0]
    if used:
        result["first_activity"] = _clock(used[0]["started_at"])
        result["last_activity"] = _clock(used[-1]["ended_at"])

    view = workday_service.describe(ledger.conn, ledger.config, day)
    if view:
        result["workday"] = {
            "status": view.session["status"],
            "worked_hours": _h(view.worked_seconds),
            "paused_hours": _h(view.paused_seconds),
            "missing_data_hours": _h(view.missing_data_seconds),
        }
    alerts = list_distractions(ledger.conn, day)
    if alerts:
        result["distraction_alerts"] = len(alerts)
    span = _day_span(facts)
    if span:
        marked = absences_between(ledger.conn, span[0], span[1])
        if marked:
            result["marked_absent"] = [
                {"start": _clock(start.astimezone(span[2]).isoformat()),
                 "end": _clock(end.astimezone(span[2]).isoformat()), "label": label}
                for start, end, label in marked[:20]
            ]
    return result


def tool_range_summary(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    start, end = _range(ledger, args)
    stats = _range_stats(ledger, start, end)
    stats["per_day"] = [
        {
            "date": day,
            "recorded_hours": _h(facts.recorded_seconds),
            "effective_hours": _h(facts.effective_seconds) if facts.attention_tracked else None,
            "top_category": _first(_top(facts.totals_by("category"), 1)),
            "top_app": _first(_top(facts.totals_by("process_name"), 1, skip=UNKNOWN_APP)),
        }
        for day, facts in ((d, ledger.day(d)) for d in ledger.dates_between(start, end))
    ]
    return stats


def tool_compare_ranges(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    a_start, a_end = _range(ledger, args, "a_start", "a_end")
    b_start, b_end = _range(ledger, args, "b_start", "b_end")
    a = _range_stats(ledger, a_start, a_end)
    b = _range_stats(ledger, b_start, b_end)

    def per_day(stats: dict[str, object], category: str) -> float:
        for row in stats["categories"]:  # type: ignore[union-attr]
            if row["category"] == category:
                return float(row["hours_per_day"])
        return 0.0

    names = []
    for stats in (a, b):
        for row in stats["categories"]:  # type: ignore[union-attr]
            if row["category"] not in names:
                names.append(row["category"])
    return {
        "a": _brief(a),
        "b": _brief(b),
        "change_b_minus_a": {
            "effective_hours_per_day": _delta(
                a["effective_hours_per_day"], b["effective_hours_per_day"]
            ),
            "recorded_hours_per_day": _delta(
                a["recorded_hours_per_day"], b["recorded_hours_per_day"]
            ),
            "category_hours_per_day": {
                name: _delta(per_day(a, name), per_day(b, name)) for name in names
            },
        },
    }


def tool_app_usage(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    query = str(args.get("query") or "").strip().lower()
    if not query:
        raise ToolError("query is required, e.g. 'chrome' or 'github.com'")
    start, end = _range(ledger, args, required=False)
    matches: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    per_day = []
    recorded = 0.0
    for day in ledger.dates_between(start, end):
        facts = ledger.day(day)
        recorded += facts.recorded_seconds
        day_fg = day_eff = 0.0
        for key in ("process_name", "site"):
            for name, (fg, eff) in facts.totals_by(key).items():
                if query in name.lower():
                    matches[name][0] += fg
                    matches[name][1] += eff
                    day_fg += fg
                    day_eff += eff
        if day_fg >= 60:
            per_day.append(
                {"date": day, "hours": _h(day_fg), "effective_hours": _h(day_eff)}
            )
    if not matches:
        return {
            "query": query, "start": start, "end": end, "matches": [],
            "note": "No app or site name contains this text in the range. This tool "
                    "matches names only; for a category such as gaming use "
                    "range_summary or compare_ranges.",
        }
    total_fg = sum(fg for fg, _ in matches.values())
    total_eff = sum(eff for _, eff in matches.values())
    return {
        "query": query,
        "start": start,
        "end": end,
        "matches": [
            {"name": name, "hours": _h(fg), "effective_hours": _h(eff)}
            for name, (fg, eff) in _top(matches, 10)
        ],
        "total_hours": _h(total_fg),
        "total_effective_hours": _h(total_eff),
        "effective_share_pct": _pct(total_eff, total_fg),
        "share_of_recorded_pct": _pct(total_fg, recorded),
        "days_used": len(per_day),
        "per_day": per_day[-62:],
    }


def tool_timeline(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    day = _tracked_date(ledger, args, "date")
    min_minutes = _number(args.get("min_minutes"), default=10.0)
    window_from = str(args.get("from_time") or "00:00")
    window_to = str(args.get("to_time") or "24:00")

    blocks: list[dict[str, object]] = []
    for segment in ledger.day(day).activity:
        seconds = _seconds(segment)
        app = str(segment.get("process_name") or UNKNOWN_APP)
        last = blocks[-1] if blocks else None
        if (
            last is not None
            and last["app"] == app
            and _gap_seconds(last["_end"], segment["started_at"]) <= 120
        ):
            last["_end"] = segment["ended_at"]
            last["_seconds"] += seconds
            last["_effective"] += seconds * float(segment.get("attention_weight") or 0.0)
            last["_titles"][str(segment.get("window_title") or "")] += seconds
            continue
        titles: Counter = Counter()
        titles[str(segment.get("window_title") or "")] += seconds
        blocks.append({
            "app": app,
            "category": segment.get("category"),
            "_start": segment["started_at"],
            "_end": segment["ended_at"],
            "_seconds": seconds,
            "_effective": seconds * float(segment.get("attention_weight") or 0.0),
            "_titles": titles,
        })

    kept = [
        b for b in blocks
        if b["_seconds"] >= min_minutes * 60
        and window_from <= _clock(b["_start"]) < window_to
    ]
    truncated = len(kept) > 40
    if truncated:
        longest = sorted(kept, key=lambda b: b["_seconds"], reverse=True)[:40]
        kept = [b for b in kept if b in longest]
    return {
        "date": day,
        "min_minutes": min_minutes,
        "blocks": [
            {
                "start": _clock(b["_start"]),
                "end": _clock(b["_end"]),
                "minutes": round(b["_seconds"] / 60),
                "app": b["app"],
                "category": b["category"],
                "effective_pct": _pct(b["_effective"], b["_seconds"]),
                "main_title": b["_titles"].most_common(1)[0][0][:80],
            }
            for b in kept
        ],
        "truncated_to_longest_40": truncated,
    }


def tool_hourly_profile(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    start, end = _range(ledger, args, required=False)
    effective: Counter = Counter()
    active: Counter = Counter()
    categories: dict[int, Counter] = defaultdict(Counter)
    days = 0
    for day in ledger.dates_between(start, end):
        facts = ledger.day(day)
        if not facts.attention_tracked:
            continue
        days += 1
        for segment in facts.attention:
            weight = float(segment.get("attention_weight") or 0.0)
            for hour, seconds in _split_by_hour(segment):
                effective[hour] += seconds * weight
        for segment in facts.activity:
            if segment.get("process_name") == UNKNOWN_APP:
                continue
            for hour, seconds in _split_by_hour(segment):
                active[hour] += seconds
                categories[hour][str(segment.get("category"))] += seconds
    if not days:
        return {"start": start, "end": end, "days_averaged": 0,
                "note": "No day in this range has keyboard/mouse attention data."}
    hours = [
        {
            "hour": f"{hour:02d}:00",
            "avg_effective_minutes": round(effective[hour] / days / 60, 1),
            "avg_app_minutes": round(active[hour] / days / 60, 1),
            "top_category": (categories[hour].most_common(1)[0][0] if categories[hour] else None),
        }
        for hour in range(24)
    ]
    ranked = sorted(hours, key=lambda row: row["avg_effective_minutes"], reverse=True)
    return {
        "start": start,
        "end": end,
        "days_averaged": days,
        "most_focused_hours": [row["hour"] for row in ranked[:3]],
        "least_focused_hours": [row["hour"] for row in ranked[-3:]],
        "hours": hours,
    }


def tool_deliverables(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    deliverables = list_deliverables(ledger.conn)
    if not deliverables:
        return {"deliverables": [],
                "note": "No deliverables are defined, so time cannot be linked to goals."}
    dates = ledger.tracked_dates()
    default_start = dates[-7] if len(dates) >= 7 else (dates[0] if dates else "")
    start, end = _range(ledger, {**args, "start": args.get("start") or default_start},
                        required=False)
    attributed: dict[str, float] = defaultdict(float)
    for day in ledger.dates_between(start, end):
        for entry in attribute_time(ledger.conn, ledger.day(day).activity):
            attributed[str(entry["id"])] += float(entry["effective_seconds"])
    open_tasks = list_tasks(ledger.conn, include_done=False)
    return {
        "start": start,
        "end": end,
        "deliverables": [
            {
                "title": d["title"],
                "status": d["status"],
                "priority": d["priority"],
                "due_date": d["due_date"],
                "effective_hours_in_range": _h(attributed.get(str(d["id"]), 0.0)),
                "open_tasks": [
                    t["title"] for t in open_tasks if t["deliverable_id"] == d["id"]
                ][:8],
            }
            for d in deliverables
        ],
    }


_AWAITING = (
    "Nothing has changed yet. Tell the user exactly what this would change, using "
    "the preview figures, and that it needs their approval."
)


def tool_mark_absent(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    day = _tracked_date(ledger, args, "date")
    label = str(args.get("label") or "").strip().lower()
    if label not in ABSENCE_LABELS:
        raise ToolError(f"label must be one of: {', '.join(ABSENCE_LABELS)}")
    facts = ledger.day(day)
    app = str(args.get("app") or "").strip()
    if app:
        ranges = _app_ranges(facts, app)
        if not ranges:
            names = [name for name, _ in _top(facts.totals_by("process_name"), 10)]
            raise ToolError(f"'{app}' was never in front on {day}; apps that day: {', '.join(names)}")
        what = f"every stretch when {app} was in front"
    else:
        ranges = [_clock_range(facts, day, args)]
        what = f"{_clock(ranges[0][0].isoformat())}-{_clock(ranges[0][1].isoformat())}"
    preview = _absence_preview(facts, ranges)
    proposal = Proposal(
        action="mark_absent",
        arguments={
            "date": day,
            "label": label,
            "app": app or None,
            "note": str(args.get("note") or "") or None,
            "ranges": [[s.astimezone(timezone.utc).isoformat(), e.astimezone(timezone.utc).isoformat()]
                       for s, e in ranges],
        },
        summary=f"Mark {what} on {day} as {label} ({preview['marked_hours']} h)",
        preview=preview,
    )
    ledger.proposals.append(proposal)
    return {"status": "awaiting_user_approval", "proposal_id": proposal.id,
            "summary": proposal.summary, "preview": preview, "note": _AWAITING}


def tool_correct_category(ledger: Ledger, args: dict[str, object]) -> dict[str, object]:
    scope = str(args.get("scope") or "").strip()
    pattern = str(args.get("pattern") or "").strip().lower()
    category = str(args.get("category") or "").strip()
    if scope not in SCOPES:
        raise ToolError(f"scope must be one of: {', '.join(SCOPES)}")
    if not pattern:
        raise ToolError("pattern is required, e.g. 'explorer.exe' or 'youtube.com'")
    if category not in CATEGORIES:
        raise ToolError(f"category must be one of: {', '.join(CATEGORIES)}")
    current = ledger._rules.get("corrections", {}).get(scope, {}).get(pattern)  # type: ignore[union-attr]

    recent = ledger.tracked_dates()[-7:]
    hours: Counter = Counter()
    for day in recent:
        for segment in ledger.day(day).activity:
            if _matches_scope(segment, scope, pattern):
                hours[str(segment.get("category"))] += _seconds(segment)
    preview = {
        "scope": scope,
        "pattern": pattern,
        "new_category": category,
        "replaces_correction": current,
        "last_7_tracked_days": {
            "start": recent[0] if recent else None,
            "end": recent[-1] if recent else None,
            "matching_hours": _h(sum(hours.values())),
            "current_categories": {name: _h(sec) for name, sec in hours.most_common()},
        },
    }
    proposal = Proposal(
        action="correct_category",
        arguments={"scope": scope, "pattern": pattern, "category": category},
        summary=f"Always classify {scope} '{pattern}' as {category}"
                + (f" (replacing {current})" if current else ""),
        preview=preview,
    )
    ledger.proposals.append(proposal)
    return {"status": "awaiting_user_approval", "proposal_id": proposal.id,
            "summary": proposal.summary, "preview": preview, "note": _AWAITING}


_DATE = "Local date, YYYY-MM-DD."
TOOLS: tuple[Tool, ...] = (
    Tool(
        "list_days",
        "Index of the tracked data: first and last tracked date, untracked gaps, "
        "when keyboard/mouse attention tracking began, and ready-made ranges for "
        "the last 7 and previous 7 tracked days. Call this first.",
        _params(),
        tool_list_days,
    ),
    Tool(
        "day_summary",
        "One day in detail: recorded and effective hours, attention states, time "
        "per category, top apps and sites, focus blocks of 25+ minutes, first and "
        "last activity, workday and distraction alerts.",
        _params(("date",), date=_DATE),
        tool_day_summary,
    ),
    Tool(
        "range_summary",
        "Totals and per-day averages over a date range: recorded and effective "
        "hours, time per category, top apps, best and worst day, and one line per day.",
        _params(("start", "end"), start=_DATE, end=_DATE),
        tool_range_summary,
    ),
    Tool(
        "compare_ranges",
        "Compare two date ranges (a = earlier, b = later). Returns both summaries "
        "and the already computed per-day changes, including per category.",
        _params(
            ("a_start", "a_end", "b_start", "b_end"),
            a_start=_DATE, a_end=_DATE, b_start=_DATE, b_end=_DATE,
        ),
        tool_compare_ranges,
    ),
    Tool(
        "app_usage",
        "Time spent in apps or websites whose name contains `query` (e.g. "
        "'chrome', 'terraria', 'github.com'), per day and in total. Matches names, "
        "not categories. Range defaults to all tracked days.",
        _params(("query",), query="Part of an app or site name.", start=_DATE, end=_DATE),
        tool_app_usage,
    ),
    Tool(
        "timeline",
        "What happened during one day, in order: blocks of continuous use of one "
        "app with start/end time, category, share of effective attention and the "
        "main window title. Use from_time/to_time (HH:MM) to zoom in.",
        _params(
            ("date",),
            date=_DATE,
            from_time="Optional start of the window, HH:MM.",
            to_time="Optional end of the window, HH:MM.",
            min_minutes="Optional: hide blocks shorter than this (default 10).",
        ),
        tool_timeline,
    ),
    Tool(
        "hourly_profile",
        "Daily rhythm: average effective and app minutes for each hour of the "
        "day, plus the most and least focused hours. Range defaults to all "
        "tracked days.",
        _params(start=_DATE, end=_DATE),
        tool_hourly_profile,
    ),
    Tool(
        "deliverables",
        "The user's deliverables (goals) with status, due date, open tasks and "
        "effective hours linked to each in a range (default: last 7 tracked days).",
        _params(start=_DATE, end=_DATE),
        tool_deliverables,
    ),
    Tool(
        "mark_absent",
        "PROPOSE marking time on one day as time the user was away from the computer "
        f"(label: {', '.join(ABSENCE_LABELS)}). Marked time counts as idle with no "
        "effective attention. Give either app (e.g. '(unknown)' for the lock screen) to "
        "mark every stretch that app was in front, or start_time and end_time (HH:MM; an "
        "end before the start runs past midnight). When the user names an app, pass app "
        "rather than clock times read off the timeline, so no stretch is missed. "
        "Nothing is written until the user approves.",
        _params(
            ("date", "label"),
            date=_DATE,
            label=f"One of {', '.join(ABSENCE_LABELS)}; use the word the user said "
            "(离线 offline, 睡觉 sleep, 外出 away, 吃饭 meal, 休息 break).",
            app="Optional: mark every stretch this app was in front.",
            start_time="Optional start, HH:MM (use with end_time).",
            end_time="Optional end, HH:MM.",
            note="Optional short note from the user.",
        ),
        tool_mark_absent,
    ),
    Tool(
        "correct_category",
        "PROPOSE always classifying an app (scope 'process', e.g. 'explorer.exe'), a "
        "site (scope 'site', e.g. 'youtube.com') or a title keyword (scope "
        f"'title_keyword') as a category ({', '.join(c for c in CATEGORIES if c != UNKNOWN)}). "
        "Re-explains all history. Nothing is written until the user approves.",
        _params(("scope", "pattern", "category"),
                scope="process, site or title_keyword.",
                pattern="The app name, site or keyword.",
                category="The category to use."),
        tool_correct_category,
    ),
)
WRITE_TOOLS = frozenset({"mark_absent", "correct_category"})
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def run_tool(ledger: Ledger, name: str, arguments: dict[str, object]) -> dict[str, object]:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return {"error": f"unknown tool '{name}'; available: {', '.join(TOOLS_BY_NAME)}"}
    try:
        return tool.handler(ledger, arguments)
    except ToolError as exc:
        return {"error": str(exc)}


# --------------------------------------------------------------------------
# the conversation loop
# --------------------------------------------------------------------------
@dataclass
class ToolCall:
    name: str
    arguments: dict[str, object]
    seconds: float
    result: dict[str, object]


@dataclass
class AnalysisResult:
    question: str
    model: str
    answer: str
    stop_reason: str
    rounds: int
    seconds: float
    tool_mode: str = "native"
    tool_calls: list[ToolCall] = field(default_factory=list)
    ungrounded_numbers: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    prompt_tokens: int = 0
    output_tokens: int = 0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "model": self.model,
            "answer": self.answer,
            "stop_reason": self.stop_reason,
            "tool_mode": self.tool_mode,
            "rounds": self.rounds,
            "seconds": round(self.seconds, 1),
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments, "seconds": round(c.seconds, 2),
                 "result": c.result}
                for c in self.tool_calls
            ],
            "ungrounded_numbers": self.ungrounded_numbers,
            "corrections": self.corrections,
            "thinking": self.thinking,
            "proposals": [p.to_json_dict() for p in self.proposals],
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
        }


def analyst_model(config) -> str:
    return str(getattr(config, "llm_analyst_model", "") or getattr(config, "llm_model", ""))


def analyze(
    conn: Connection,
    config,
    question: str,
    *,
    model: str | None = None,
    think: bool | None = None,
    max_rounds: int = DEFAULT_ROUNDS,
    num_ctx: int = DEFAULT_NUM_CTX,
    timeout: float | None = None,
    tool_mode: str = "auto",
    history: list[dict[str, str]] | None = None,
    on_tool_call: Callable[[ToolCall], None] | None = None,
    on_thinking: Callable[[str], None] | None = None,
    on_proposal: Callable[[Proposal], None] | None = None,
) -> AnalysisResult:
    """Let the local model answer ``question`` by calling the tools above.

    ``tool_mode`` is "native" (Ollama's tool-calling API), "text" (the model
    writes a JSON call into its reply) or "auto", which picks per model.
    ``history`` holds earlier ``{"question", "answer"}`` turns of the same
    conversation, so a follow-up such as "mark that day" can be resolved.
    Write tools only add proposals to the result; nothing is written here.
    Raises ``llm_service.LlmUnavailable`` when the model cannot be used.
    """
    model = model or analyst_model(config)
    timeout = timeout or max(float(getattr(config, "llm_timeout_seconds", 120)), 300.0)
    mode = resolve_tool_mode(config, model, tool_mode, timeout)
    ledger = Ledger(conn, config)
    system = _system_prompt(ledger, config)
    if mode == "text":
        system += "\n\n" + _text_protocol()
    earlier = [turn for turn in (history or []) if turn.get("question") and turn.get("answer")]
    messages: list[dict[str, object]] = [{"role": "system", "content": system}]
    for turn in earlier[-MAX_HISTORY_TURNS:]:
        messages.append({"role": "user", "content": str(turn["question"])})
        messages.append({"role": "assistant", "content": str(turn["answer"])})
    messages.append({"role": "user", "content": question})
    schemas = [tool.schema() for tool in TOOLS]
    result = AnalysisResult(question, model, "", "no_answer", 0, 0.0, tool_mode=mode)
    seen: dict[str, dict[str, object]] = {}
    started = time.perf_counter()

    def ask(with_tools: bool) -> dict[str, object]:
        reply = llm_service.chat(
            config, messages, model=model,
            tools=schemas if with_tools and mode == "native" else None,
            think=think, num_ctx=num_ctx, timeout=timeout,
        )
        result.rounds += 1
        result.prompt_tokens += int(reply.get("prompt_eval_count") or 0)
        result.output_tokens += int(reply.get("eval_count") or 0)
        message: dict[str, object] = reply["message"]  # type: ignore[assignment]
        # Thinking arrives in its own field, or inside <think> in older templates.
        thought = "\n".join(
            part.strip()
            for part in [str(message.get("thinking") or ""),
                         *re.findall(r"<think>(.*?)</think>", str(message.get("content") or ""), re.DOTALL)]
            if part.strip()
        )
        if thought:
            result.thinking.append(thought)
            if on_thinking:
                on_thinking(thought)
        return message

    def sources() -> list[str]:
        return ([system, question]
                + [f"{t['question']}\n{t['answer']}" for t in earlier]
                + [json.dumps(c.result) for c in result.tool_calls])

    for _ in range(max_rounds):
        message = ask(with_tools=True)
        calls = _requested_calls(message, mode)
        if not calls:
            answer = _strip_thinking(str(message.get("content") or ""))
            if not answer:
                messages.append({"role": "user", "content": "Please give your final answer now."})
                continue
            correction = None
            if not result.tool_calls:
                reminder = _NO_TOOLS_CORRECTION
                if mode == "text":
                    reminder += ' Reply with only {"tool": "list_days", "arguments": {}} to start.'
                correction = ("answered without calling a tool", reminder)
            else:
                stray = ungrounded_numbers(answer, sources())
                if stray:
                    correction = (
                        f"numbers not from any tool: {', '.join(stray)}",
                        _GROUNDING_CORRECTION.format(numbers=", ".join(stray)),
                    )
            if correction and len(result.corrections) < MAX_CORRECTIONS:
                result.corrections.append(correction[0])
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "user", "content": correction[1]})
                continue
            result.answer, result.stop_reason = answer, "answered"
            break
        echo: dict[str, object] = {"role": "assistant", "content": str(message.get("content") or "")}
        if mode == "native":
            echo["tool_calls"] = message.get("tool_calls")
        messages.append(echo)
        for name, arguments in calls:
            key = json.dumps([name, arguments], sort_keys=True)
            tool_started = time.perf_counter()
            proposed = len(ledger.proposals)
            # A repeated read is served from cache; a repeated write proposal is not.
            output = (seen.get(key) if name not in WRITE_TOOLS else None) or run_tool(ledger, name, arguments)
            seen[key] = output
            for proposal in ledger.proposals[proposed:]:
                result.proposals.append(proposal)
                if on_proposal:
                    on_proposal(proposal)
            record = ToolCall(name, arguments, time.perf_counter() - tool_started, output)
            result.tool_calls.append(record)
            if on_tool_call:
                on_tool_call(record)
            content = json.dumps(output, ensure_ascii=False)
            if mode == "native":
                messages.append({"role": "tool", "tool_name": name, "content": content})
            else:
                messages.append({"role": "user", "content": f"Result of {name}:\n{content}"})
    else:
        messages.append({
            "role": "user",
            "content": "Stop calling tools. Answer now using only the data above.",
        })
        answer = _strip_thinking(str(ask(with_tools=False).get("content") or ""))
        result.answer, result.stop_reason = answer, ("round_limit" if answer else "no_answer")

    result.seconds = time.perf_counter() - started
    result.ungrounded_numbers = ungrounded_numbers(result.answer, sources())
    return result


TOOL_MODES = ("auto", "native", "text")
_PING_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Check that tool calls work.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
_NATIVE_TOOL_CALLS: dict[tuple[str, str], bool] = {}


def resolve_tool_mode(config, model: str, requested: str = "auto",
                      timeout: float | None = None) -> str:
    """Pick how ``model`` calls tools; "auto" probes it once per process."""
    if requested not in TOOL_MODES:
        raise ValueError(f"tool mode must be one of {', '.join(TOOL_MODES)}")
    capabilities = llm_service.model_capabilities(config, model)
    if requested == "native" and "tools" not in capabilities:
        raise llm_service.LlmUnavailable(
            f"model '{model}' has no native tool calling; use the text tool mode"
        )
    if requested != "auto":
        return requested
    if "tools" not in capabilities:
        return "text"
    key = (str(getattr(config, "llm_base_url", "")), model)
    if key not in _NATIVE_TOOL_CALLS:
        _NATIVE_TOOL_CALLS[key] = _emits_native_tool_calls(config, model, capabilities, timeout)
    return "native" if _NATIVE_TOOL_CALLS[key] else "text"


def _emits_native_tool_calls(config, model: str, capabilities: tuple[str, ...],
                             timeout: float | None) -> bool:
    """Ask for one trivial tool call.

    Some models list tool support yet answer in prose instead: deepseek-r1:8b
    under Ollama 0.40 never emitted a tool call when measured, with or without
    thinking, while it follows the text protocol reliably.
    """
    reply = llm_service.chat(
        config,
        [{"role": "user", "content": "Call the ping tool now."}],
        model=model,
        tools=[_PING_TOOL],
        think=False if "thinking" in capabilities else None,
        timeout=timeout,
    )
    return bool(reply["message"].get("tool_calls"))  # type: ignore[union-attr]


def _requested_calls(message: dict[str, object], mode: str) -> list[tuple[str, dict[str, object]]]:
    if mode == "native":
        return [
            (str((call.get("function") or {}).get("name") or ""),
             _arguments((call.get("function") or {}).get("arguments")))
            for call in message.get("tool_calls") or []  # type: ignore[union-attr]
        ]
    # Text mode: the first JSON object with a "tool" key is the call.
    text = _strip_thinking(str(message.get("content") or ""))
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("tool"), str):
            return [(value["tool"], _arguments(value.get("arguments")))]
    return []


def _text_protocol() -> str:
    lines = [
        "How to call a tool: reply with only this JSON object and nothing else:",
        '{"tool": "<tool name>", "arguments": {"<name>": "<value>"}}',
        "The result arrives in the next message. Call one tool per reply. When you have "
        "what you need, reply with the final answer as plain text and no JSON.",
        "Tools (dates are YYYY-MM-DD, ? marks an optional argument):",
    ]
    for tool in TOOLS:
        properties = tool.parameters["properties"]  # type: ignore[index]
        required = tool.parameters["required"]  # type: ignore[index]
        signature = ", ".join(
            name if name in required else f"{name}?" for name in properties  # type: ignore[union-attr]
        )
        lines.append(f"- {tool.name}({signature}): {tool.description}")
    return "\n".join(lines)


def _system_prompt(ledger: Ledger, config) -> str:
    dates = ledger.tracked_dates()
    coverage = (
        f"Tracked data covers {dates[0]} to {dates[-1]} ({len(dates)} days with data)."
        if dates else "Nothing has been tracked yet."
    )
    language = (
        "Answer in Simplified Chinese."
        if str(getattr(config, "language", "en")).startswith("zh")
        else "Answer in English."
    )
    return "\n".join([
        "You are the analyst inside FocusLedger, a local tool that records which app "
        "is in front on this computer every second and how long ago the keyboard or "
        "mouse was last used.",
        "Terms: recorded hours = time the app was in front. Effective hours = recorded "
        "time weighted by attention (active input 1.0, low activity 0.5, longer idle 0); "
        "it estimates real engagement. App '(unknown)' means the lock screen or system UI. "
        "Categories: productive_core, learning_general, neutral_educational, admin, "
        "communication, social, entertainment, gaming, unknown (not classified yet).",
        "Rules:",
        "1. Get every fact from the tools. Call list_days first unless you already know "
        "the dates you need.",
        "2. Never do arithmetic. Every number you write must appear in a tool result, "
        "in the same unit. For totals, averages, shares or changes, call the tool that "
        "returns them (range_summary, compare_ranges, app_usage).",
        "3. Days with attention_tracked false have no keyboard/mouse data: their "
        "effective time is unknown, not zero.",
        "4. If the question asks about a period with no data, say so and use the "
        "closest tracked period, naming its dates.",
        "5. Lead with the direct answer, then 2-5 concrete supporting points. No "
        "praise or motivational filler.",
        "6. You cannot change data yourself. mark_absent and correct_category only "
        "propose a change, which the user approves or rejects in the interface. When "
        "the user asks to correct or relabel their data, call one of them, then say "
        "exactly what will change (use the preview figures) and that it awaits their "
        "approval. Never claim a change has been made.",
        "7. Earlier turns of this conversation are context: resolve 'that day', 'it' "
        "and similar references from them.",
        f"Today is {date.today().isoformat()}. {coverage}",
        language,
    ])


# --------------------------------------------------------------------------
# grounding check
# --------------------------------------------------------------------------
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def ungrounded_numbers(answer: str, sources: list[str]) -> list[str]:
    """Numbers in ``answer`` that no source contains, allowing for rounding.

    Integers up to 10 are skipped: they are list markers and small counts far
    more often than figures.
    """
    known = [float(token) for text in sources for token in _NUMBER.findall(text)]
    flagged: list[str] = []
    for token in _NUMBER.findall(answer):
        value = float(token)
        if value <= 10 and value.is_integer():
            continue
        if any(_matches(value, k) for k in known):
            continue
        if token not in flagged:
            flagged.append(token)
    return flagged


def _matches(value: float, known: float) -> bool:
    if abs(value - known) < 0.051:
        return True
    return value.is_integer() and value == round(known)


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EvalQuestion:
    text: str
    expect_tools: tuple[str, ...]


DEFAULT_EVAL_QUESTIONS = (
    EvalQuestion("我有记录的最近 7 天，时间主要花在了哪些类别和应用上？", ("range_summary",)),
    EvalQuestion("我有记录的最后一天都做了什么？哪段时间最专注？", ("day_summary", "timeline")),
    EvalQuestion(
        "把最近 7 个有记录的日子和再往前 7 个有记录的日子对比，游戏时间和有效时间有什么变化？",
        ("compare_ranges",),
    ),
    EvalQuestion("一天里我通常哪几个小时最专注？", ("hourly_profile",)),
    EvalQuestion("我总共在 Chrome 上花了多少时间，其中有效时间占多少？", ("app_usage",)),
)


@dataclass
class EvalRow:
    model: str
    index: int
    question: EvalQuestion
    result: AnalysisResult | None
    error: str | None = None

    @property
    def answered(self) -> bool:
        return bool(self.result and self.result.answer)

    @property
    def used_expected_tool(self) -> bool:
        used = {c.name for c in self.result.tool_calls} if self.result else set()
        return bool(used & set(self.question.expect_tools))


def run_eval(
    conn: Connection,
    config,
    models: list[str],
    questions: tuple[EvalQuestion, ...] | list[EvalQuestion] = DEFAULT_EVAL_QUESTIONS,
    *,
    on_row: Callable[[EvalRow], None] | None = None,
    **options,
) -> list[EvalRow]:
    rows: list[EvalRow] = []
    for model in models:
        for index, question in enumerate(questions, start=1):
            try:
                result = analyze(conn, config, question.text, model=model, **options)
                row = EvalRow(model, index, question, result)
            except llm_service.LlmUnavailable as exc:
                row = EvalRow(model, index, question, None, str(exc))
            rows.append(row)
            if on_row:
                on_row(row)
    return rows


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _range_stats(ledger: Ledger, start: str, end: str) -> dict[str, object]:
    dates = ledger.dates_between(start, end)
    if not dates:
        raise ToolError(
            f"no tracked days between {start} and {end}; tracked dates run from "
            f"{_bounds(ledger)}"
        )
    if len(dates) > MAX_RANGE_DAYS:
        raise ToolError(f"range has {len(dates)} tracked days; use at most {MAX_RANGE_DAYS}")
    recorded = effective = 0.0
    attention_days = 0
    categories: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    apps: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    daily_effective: list[tuple[str, float]] = []
    for day in dates:
        facts = ledger.day(day)
        recorded += facts.recorded_seconds
        if facts.attention_tracked:
            attention_days += 1
            effective += facts.effective_seconds
            daily_effective.append((day, facts.effective_seconds))
        for name, (fg, eff) in facts.totals_by("category").items():
            categories[name][0] += fg
            categories[name][1] += eff
        for name, (fg, eff) in facts.totals_by("process_name").items():
            apps[name][0] += fg
            apps[name][1] += eff
    days = len(dates)
    daily_effective.sort(key=lambda item: item[1])
    stats: dict[str, object] = {
        "start": start,
        "end": end,
        "tracked_days": days,
        "days_with_attention_data": attention_days,
        "untracked_ranges": _gaps(dates, start, end),
        "recorded_hours": _h(recorded),
        "recorded_hours_per_day": _h(recorded / days),
        "effective_hours": _h(effective) if attention_days else None,
        "effective_hours_per_day": _h(effective / attention_days) if attention_days else None,
        "categories": [
            {
                "category": name,
                "hours": _h(fg),
                "hours_per_day": _h(fg / days),
                "effective_hours": _h(eff),
                "share_of_recorded_pct": _pct(fg, recorded),
            }
            for name, (fg, eff) in _top(categories, 9)
        ],
        "top_apps": [
            {"app": name, "hours": _h(fg), "effective_hours": _h(eff)}
            for name, (fg, eff) in _top(apps, 10, skip=UNKNOWN_APP)
        ],
        "locked_or_system_hours": _h(apps.get(UNKNOWN_APP, [0.0])[0]),
    }
    if daily_effective:
        stats["most_effective_day"] = {
            "date": daily_effective[-1][0], "effective_hours": _h(daily_effective[-1][1])
        }
        stats["least_effective_day"] = {
            "date": daily_effective[0][0], "effective_hours": _h(daily_effective[0][1])
        }
    return stats


def _brief(stats: dict[str, object]) -> dict[str, object]:
    keep = ("start", "end", "tracked_days", "days_with_attention_data", "recorded_hours",
            "recorded_hours_per_day", "effective_hours", "effective_hours_per_day")
    brief = {key: stats[key] for key in keep}
    brief["categories"] = stats["categories"]
    brief["top_apps"] = stats["top_apps"][:5]  # type: ignore[index]
    return brief


def _delta(before: object, after: object) -> dict[str, object] | None:
    if before is None or after is None:
        return None
    before, after = float(before), float(after)
    change = {"hours": round(after - before, 1)}
    if before:
        change["pct"] = round((after - before) / before * 100, 1)
    return change


def _focus_blocks(facts: DayFacts) -> list[dict[str, object]]:
    """Stretches of 25+ minutes of active or low-activity attention, longest first."""
    blocks: list[list[dict[str, object]]] = []
    for segment in facts.attention:
        if _seconds(segment) <= 0:
            continue
        engaged = float(segment.get("attention_weight") or 0.0) >= 0.5
        if not engaged:
            blocks.append([])
            continue
        if blocks and blocks[-1] and _gap_seconds(blocks[-1][-1]["ended_at"],
                                                  segment["started_at"]) <= 60:
            blocks[-1].append(segment)
        else:
            blocks.append([segment])
    found = []
    for block in blocks:
        if not block:
            continue
        seconds = sum(_seconds(s) for s in block)
        if seconds < FOCUS_BLOCK_MINUTES * 60:
            continue
        start, end = _utc(block[0]["started_at"]), _utc(block[-1]["ended_at"])
        apps: Counter = Counter()
        for segment in facts.activity:
            if segment.get("process_name") == UNKNOWN_APP:
                continue
            shared = (min(end, _utc(segment["ended_at"]))
                      - max(start, _utc(segment["started_at"]))).total_seconds()
            if shared > 0:
                apps[str(segment.get("process_name"))] += shared
        found.append({
            "start": _clock(block[0]["started_at"]),
            "end": _clock(block[-1]["ended_at"]),
            "minutes": round(seconds / 60),
            "main_app": apps.most_common(1)[0][0] if apps else None,
        })
    found.sort(key=lambda b: b["minutes"], reverse=True)
    return found[:5]


def _day_zone(facts: DayFacts) -> tzinfo:
    """The UTC offset the day was recorded in, or this machine's own."""
    for segment in [*facts.activity, *facts.attention]:
        zone = datetime.fromisoformat(str(segment["started_at"])).tzinfo
        if zone is not None:
            return zone
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def _day_span(facts: DayFacts) -> tuple[datetime, datetime, tzinfo] | None:
    segments = [*facts.activity, *facts.attention]
    if not segments:
        return None
    start = min(_utc(s.get("started_at_utc") or s["started_at"]) for s in segments)
    end = max(_utc(s.get("ended_at_utc") or s["ended_at"]) for s in segments)
    return start, end + timedelta(seconds=1), _day_zone(facts)


def _app_ranges(facts: DayFacts, app: str) -> list[tuple[datetime, datetime]]:
    """Every stretch ``app`` was in front, joining stretches under 2 minutes apart."""
    wanted = app.lower()
    ranges: list[list[datetime]] = []
    for segment in facts.activity:
        if str(segment.get("process_name") or "").lower() != wanted:
            continue
        start = _utc(segment.get("started_at_utc") or segment["started_at"])
        end = _utc(segment.get("ended_at_utc") or segment["ended_at"])
        if ranges and (start - ranges[-1][1]).total_seconds() <= 120:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])
    return [(start, end) for start, end in ranges if end > start]


def _clock_range(facts: DayFacts, day: str, args: dict[str, object]) -> tuple[datetime, datetime]:
    start_text = str(args.get("start_time") or "").strip()
    end_text = str(args.get("end_time") or "").strip()
    if not start_text or not end_text:
        raise ToolError("give app, or both start_time and end_time as HH:MM")
    zone = _day_zone(facts)
    start, end = _local_time(day, start_text, zone), _local_time(day, end_text, zone)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _local_time(day: str, text: str, zone: tzinfo) -> datetime:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ToolError(f"time must be HH:MM, got {text!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    base = date.fromisoformat(day)
    if (hour, minute) == (24, 0):
        return datetime.combine(base + timedelta(days=1), day_time.min, tzinfo=zone)
    if hour > 23 or minute > 59:
        raise ToolError(f"time must be HH:MM, got {text!r}")
    return datetime.combine(base, day_time(hour, minute), tzinfo=zone)


def _absence_preview(facts: DayFacts, ranges: list[tuple[datetime, datetime]]) -> dict[str, object]:
    """What marking ``ranges`` as away would change on this day."""
    def shared(segment: dict[str, object]) -> float:
        start = _utc(segment.get("started_at_utc") or segment["started_at"])
        end = _utc(segment.get("ended_at_utc") or segment["ended_at"])
        return sum(max(0.0, (min(end, e) - max(start, s)).total_seconds()) for s, e in ranges)

    removed = sum(
        shared(segment) * float(segment.get("attention_weight") or 0.0)
        for segment in facts.attention
        if float(segment.get("attention_weight") or 0.0) > 0
    )
    apps: Counter = Counter()
    for segment in facts.activity:
        apps[str(segment.get("process_name") or UNKNOWN_APP)] += shared(segment)
    zone = _day_zone(facts)
    before = facts.effective_seconds
    return {
        "date": facts.date,
        "stretches": len(ranges),
        "from": _clock(min(s for s, _ in ranges).astimezone(zone).isoformat()),
        "to": _clock(max(e for _, e in ranges).astimezone(zone).isoformat()),
        "marked_hours": _h(sum((e - s).total_seconds() for s, e in ranges)),
        "effective_hours_before": _h(before) if facts.attention_tracked else None,
        "effective_hours_after": _h(max(0.0, before - removed)) if facts.attention_tracked else None,
        # Under 3 minutes would show as 0.0 h.
        "app_hours_inside": [
            {"app": name, "hours": _h(seconds)} for name, seconds in apps.most_common(5) if seconds >= 180
        ],
    }


def _matches_scope(segment: dict[str, object], scope: str, pattern: str) -> bool:
    if scope == "process":
        return str(segment.get("process_name") or "").lower() == pattern
    if scope == "site":
        site = str(segment.get("site") or "")
        return site == pattern or site.endswith("." + pattern)
    return pattern in str(segment.get("window_title") or "").lower()


def _split_by_hour(segment: dict[str, object]):
    start = datetime.fromisoformat(str(segment["started_at"]))
    end = datetime.fromisoformat(str(segment["ended_at"]))
    if start.tzinfo and end.tzinfo:
        end = end.astimezone(start.tzinfo)
    cursor = start
    while cursor < end:
        boundary = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        stop = min(boundary, end)
        yield cursor.hour, (stop - cursor).total_seconds()
        cursor = stop


def _dominant(segments: list[dict[str, object]], key: str, value: str) -> dict[str, str]:
    weights: dict[str, Counter] = defaultdict(Counter)
    for segment in segments:
        if segment.get(key):
            weights[str(segment[key])][str(segment.get(value))] += _seconds(segment)
    return {name: counter.most_common(1)[0][0] for name, counter in weights.items()}


def _top(totals: dict[str, list[float]], limit: int, skip: str | None = None):
    items = [(name, pair) for name, pair in totals.items() if name != skip and pair[0] >= 60]
    items.sort(key=lambda item: item[1][0], reverse=True)
    return items[:limit]


def _first(items) -> str | None:
    return items[0][0] if items else None


def _gaps(dates: list[str], start: str | None = None, end: str | None = None) -> list[str]:
    """Untracked stretches as ``first..last`` strings."""
    if not dates:
        return []
    have = set(dates)
    day = date.fromisoformat(start or dates[0])
    last = date.fromisoformat(end or dates[-1])
    gaps, run = [], []
    while day <= last:
        if day.isoformat() in have:
            if run:
                gaps.append(f"{run[0]}..{run[-1]}")
                run = []
        else:
            run.append(day.isoformat())
        day += timedelta(days=1)
    if run:
        gaps.append(f"{run[0]}..{run[-1]}")
    return gaps


def _bounds(ledger: Ledger) -> str:
    dates = ledger.tracked_dates()
    return f"{dates[0]} to {dates[-1]}" if dates else "nowhere (nothing tracked yet)"


def _tracked_date(ledger: Ledger, args: dict[str, object], key: str) -> str:
    day = _date_arg(args, key, required=True)
    if day not in ledger.tracked_dates():
        raise ToolError(f"{day} has no tracked data; tracked dates run from {_bounds(ledger)}")
    return day  # type: ignore[return-value]


def _range(
    ledger: Ledger,
    args: dict[str, object],
    start_key: str = "start",
    end_key: str = "end",
    required: bool = True,
) -> tuple[str, str]:
    dates = ledger.tracked_dates()
    start = _date_arg(args, start_key, required) or (dates[0] if dates else "")
    end = _date_arg(args, end_key, required) or (dates[-1] if dates else "")
    if start > end:
        start, end = end, start
    return start, end


def _date_arg(args: dict[str, object], key: str, required: bool) -> str | None:
    value = str(args.get(key) or "").strip()
    if not value:
        if required:
            raise ToolError(f"{key} is required (YYYY-MM-DD)")
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        raise ToolError(f"{key} must be a date like 2026-08-07, got {value!r}") from None


def _number(value: object, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _arguments(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _strip_thinking(text: str) -> str:
    """Drop a ``<think>`` block some models still leave inside the content."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _seconds(segment: dict[str, object]) -> float:
    return float(segment.get("duration_seconds") or 0.0)


def _utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _gap_seconds(earlier: object, later: object) -> float:
    return (_utc(later) - _utc(earlier)).total_seconds()


def _clock(value: object) -> str:
    return datetime.fromisoformat(str(value)).strftime("%H:%M")


def _h(seconds: float) -> float:
    return round(seconds / 3600, 1)


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0
