"""Phase 8: the daily review.

Structure follows the spec's markdown skeleton. The division of labour is the
important part:

* **Python** computes every number and writes every factual section. Those
  sections are identical whether or not a model is available.
* **The local model** writes only two prose sections — the retrospective and
  tomorrow's plan — from figures that are already final.

That split is what makes a 1.5B model useful here. It is never asked to add up
seconds, read a timeline, or recall a fact; it is asked to comment on a short
briefing. When no model is reachable, deterministic prose is generated instead
and the review is still complete and saved.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from sqlite3 import Connection

from focusledger.services import llm as llm_service
from focusledger.services.activity import (
    format_duration,
    list_attention_segments,
    list_timeline,
    summarize_attention,
)
from focusledger.services.alerts import list_distractions, summarize_reasons
from focusledger.services.attention import AttentionPolicy
from focusledger.services.classify import (
    CATEGORY_LABELS,
    DISTRACTING_CATEGORIES,
    PRODUCTIVE_CATEGORIES,
    attach_attention_weight,
    classify_segments,
    load_rule_context,
    summarize_categories,
    unknown_targets,
)
from focusledger.services.deliverables import (
    attribute_time,
    get_daily_note,
    list_tasks,
    stalled_deliverables,
)
from focusledger.services import workday as workday_service

LLM_OK = "ok"
LLM_FAILED = "failed"
LLM_SKIPPED = "skipped"


# --------------------------------------------------------------------------
# digest
# --------------------------------------------------------------------------
def build_digest(conn: Connection, config, workday_key: str) -> dict[str, object]:
    """Collect everything a review needs, with all arithmetic already done."""
    from datetime import date as _date

    day = _date.fromisoformat(workday_key)
    policy = AttentionPolicy.from_config(config)

    attention = summarize_attention(conn, day, config.poll_interval_seconds, policy)
    attention_segments = list_attention_segments(conn, day, policy)
    activity = list_timeline(conn, day)

    rules = load_rule_context(conn)
    classified = classify_segments(activity, rules)
    attach_attention_weight(classified, attention_segments)

    categories = summarize_categories(classified)
    view = workday_service.describe(conn, config, workday_key)
    note = get_daily_note(conn, workday_key)
    attribution = attribute_time(conn, classified)
    stalled = stalled_deliverables(conn, attribution)
    distractions = list_distractions(conn, workday_key)

    productive = sum(categories.get(c, 0.0) for c in PRODUCTIVE_CATEGORIES)
    distracting = sum(categories.get(c, 0.0) for c in DISTRACTING_CATEGORIES)

    return {
        "workday_key": workday_key,
        "workday": view,
        "attention": attention,
        "categories": categories,
        "classified": classified,
        "productive_seconds": productive,
        "distracting_seconds": distracting,
        "attribution": attribution,
        "stalled": stalled,
        "open_tasks": list_tasks(conn, include_done=False),
        "distractions": distractions,
        "distraction_reasons": summarize_reasons(distractions),
        "note": note,
        "unknown": unknown_targets(classified)[:5],
    }


# --------------------------------------------------------------------------
# deterministic sections
# --------------------------------------------------------------------------
def _fmt(seconds: float) -> str:
    return format_duration(float(seconds or 0.0))


def render_objective_sections(digest: dict[str, object]) -> str:
    view = digest["workday"]
    attention = digest["attention"]
    lines: list[str] = []

    lines.append("## Workday\n")
    if view is None:
        lines.append("- No workday session was recorded for this day.")
        lines.append("- Figures below cover the whole calendar day.")
    else:
        session = view.session  # type: ignore[union-attr]
        started = str(session["started_at"])[11:16]
        ended = str(session["ended_at"])[11:16] if session["ended_at"] else "(still open)"
        lines.append(f"- Range: {started} - {ended}")
        lines.append(f"- Status: {session['status']}")
        lines.append(f"- Worked: {_fmt(view.worked_seconds)} (paused {_fmt(view.paused_seconds)})")
        if session["is_overtime"]:
            lines.append("- Overtime: yes")
    lines.append("")

    lines.append("## Objective Stats\n")
    lines.append(f"- Tracked: {_fmt(attention.get('raw_seconds', 0))}")
    lines.append(f"- Effective attention: {_fmt(attention.get('effective_seconds', 0))}")
    lines.append(f"- Core productive + learning: {_fmt(digest['productive_seconds'])}")
    lines.append(f"- Entertainment/gaming/social: {_fmt(digest['distracting_seconds'])}")
    lines.append("")
    categories = digest["categories"]
    ranked = sorted(categories.items(), key=lambda kv: kv[1], reverse=True)  # type: ignore[union-attr]
    if ranked:
        lines.append("| Category | Time |")
        lines.append("| --- | --- |")
        for category, seconds in ranked:
            if seconds <= 0:
                continue
            lines.append(f"| {CATEGORY_LABELS.get(category, category)} | {_fmt(seconds)} |")
        lines.append("")

    lines.append("## Deliverable Progress\n")
    attribution = digest["attribution"]
    if attribution:
        lines.append("| Deliverable | Time | Effective |")
        lines.append("| --- | --- | --- |")
        for entry in attribution:  # type: ignore[union-attr]
            lines.append(
                f"| {entry['title']} | {_fmt(entry['seconds'])} | {_fmt(entry['effective_seconds'])} |"
            )
    else:
        lines.append("- No activity matched any deliverable.")
    stalled = digest["stalled"]
    if stalled:
        lines.append("")
        lines.append("Not touched today:")
        for item in stalled:  # type: ignore[union-attr]
            due = f" (due {item['due_date']})" if item["due_date"] else ""
            lines.append(f"- {item['title']}{due}")
    note = digest["note"]
    if note:
        lines.append("")
        lines.append("Written progress:")
        lines.append(f"- Moved forward: {note['progress'] or '(blank)'}")  # type: ignore[index]
        lines.append(f"- Blocked: {note['blocked'] or '(blank)'}")  # type: ignore[index]
    lines.append("")

    lines.append("## Attention and Distractions\n")
    totals = attention
    for state in ("active", "low_activity", "attention_gap", "interrupted", "hard_idle"):
        seconds = totals.get(f"{state}_seconds", 0.0)
        if seconds:
            lines.append(f"- {state}: {_fmt(seconds)}")
    distractions = digest["distractions"]
    if distractions:
        lines.append("")
        lines.append(f"{len(distractions)} distraction alert(s):")  # type: ignore[arg-type]
        for event in distractions:  # type: ignore[union-attr]
            when = str(event["detected_at"])[11:16]
            reason = event["reason"] or "unlabeled"
            lines.append(
                f"- {when} {event['kind']} ({_fmt(event['duration_seconds'])}) - {reason}"
            )
    else:
        lines.append("")
        lines.append("- No distraction alerts fired.")
    lines.append("")
    return "\n".join(lines)


def render_tail_sections(digest: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("## Missing Data\n")
    view = digest["workday"]
    intervals = view.missing_intervals if view else []  # type: ignore[union-attr]
    if intervals:
        lines.append(
            f"The agent was not reporting for {_fmt(view.missing_data_seconds)} in total:"  # type: ignore[union-attr]
        )
        for interval in intervals:
            start = str(interval["started_at"])[11:16]
            end = str(interval["ended_at"])[11:16]
            lines.append(f"- {start} - {end} ({_fmt(interval['duration_seconds'])}) {interval['reason']}")
        lines.append("")
        lines.append("Statistics above only cover the time that was actually recorded.")
    else:
        lines.append("- None. The agent reported continuously.")
    lines.append("")

    lines.append("## Manual Corrections\n")
    unknown = digest["unknown"]
    if unknown:
        lines.append("Unclassified time worth labelling:")
        for entry in unknown:  # type: ignore[union-attr]
            lines.append(
                f"- {entry['scope']} `{entry['pattern']}` - {_fmt(entry['seconds'])} "
                f"(`focusledger classify correct {entry['scope']} \"{entry['pattern']}\" <category>`)"
            )
    else:
        lines.append("- Everything tracked was classified.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# prose sections
# --------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You write short, concrete daily work retrospectives. "
    "You are given figures that are already final and correct. "
    "Never invent numbers, never recalculate, never contradict the briefing. "
    "Be specific and practical. No praise, no filler, no motivational language."
)


def _briefing(digest: dict[str, object], language: str) -> str:
    lines = [
        f"Date: {digest['workday_key']}",
        f"Effective attention: {_fmt(digest['attention'].get('effective_seconds', 0))}",  # type: ignore[union-attr]
        f"Core productive + learning: {_fmt(digest['productive_seconds'])}",
        f"Entertainment/gaming/social: {_fmt(digest['distracting_seconds'])}",
    ]
    categories = sorted(
        digest["categories"].items(), key=lambda kv: kv[1], reverse=True  # type: ignore[union-attr]
    )
    top = [f"{CATEGORY_LABELS.get(c, c)} {_fmt(s)}" for c, s in categories[:5] if s > 0]
    if top:
        lines.append("Time by category: " + "; ".join(top))

    if digest["attribution"]:
        moved = "; ".join(
            f"{e['title']} {_fmt(e['effective_seconds'])}" for e in digest["attribution"][:5]  # type: ignore[union-attr,index]
        )
        lines.append("Deliverables worked on: " + moved)
    if digest["stalled"]:
        lines.append(
            "Deliverables NOT touched: "
            + "; ".join(str(d["title"]) for d in digest["stalled"][:5])  # type: ignore[union-attr,index]
        )
    if digest["open_tasks"]:
        lines.append(
            "Open tasks: "
            + "; ".join(str(t["title"]) for t in digest["open_tasks"][:8])  # type: ignore[union-attr,index]
        )
    reasons = digest["distraction_reasons"]
    if reasons:
        lines.append(
            "Distraction reasons: "
            + "; ".join(f"{k} x{v}" for k, v in reasons.items())  # type: ignore[union-attr]
        )
    note = digest["note"]
    if note:
        if note["progress"]:  # type: ignore[index]
            lines.append(f"User says they moved forward: {note['progress']}")  # type: ignore[index]
        if note["blocked"]:  # type: ignore[index]
            lines.append(f"User says they are blocked by: {note['blocked']}")  # type: ignore[index]
        if note["tomorrow"]:  # type: ignore[index]
            lines.append(f"User's own plan for tomorrow: {note['tomorrow']}")  # type: ignore[index]
    view = digest["workday"]
    if view and view.missing_data_seconds > 60:  # type: ignore[union-attr]
        lines.append(
            f"Warning: {_fmt(view.missing_data_seconds)} of the day was not recorded."  # type: ignore[union-attr]
        )

    lang_line = (
        "Write in Simplified Chinese." if language.startswith("zh") else "Write in English."
    )
    return (
        "\n".join(lines)
        + "\n\n"
        + lang_line
        + "\n"
        + "Write exactly two sections and nothing else:\n"
        + "## Review\n"
        + "3-5 bullets. What actually progressed, what leaked time, what to change. "
        + "Reference the deliverables and categories above by name.\n"
        + "## Tomorrow Plan\n"
        + "3-5 bullets, each a concrete time block naming a deliverable or task, "
        + "for example '09:30-11:00 finish X'. No vague advice."
    )


def generate_prose(digest: dict[str, object], config) -> tuple[str, str, str, str | None]:
    """Return ``(markdown, generator, llm_status, error)`` for the two prose sections."""
    if not getattr(config, "llm_enabled", False):
        return _fallback_prose(digest), "rules", LLM_SKIPPED, "llm_enabled is false"
    try:
        text = llm_service.generate(
            config,
            _SYSTEM_PROMPT,
            _briefing(digest, getattr(config, "language", "en")),
        )
    except llm_service.LlmUnavailable as exc:
        return _fallback_prose(digest), "rules", LLM_FAILED, str(exc)

    cleaned = _ensure_sections(text)
    return cleaned, f"llm:{config.llm_model}", LLM_OK, None


def _ensure_sections(text: str) -> str:
    """Small models drift; make sure both headings exist without rewriting content."""
    body = text.strip()
    if "## Review" not in body:
        body = "## Review\n\n" + body
    if "## Tomorrow Plan" not in body:
        body += "\n\n## Tomorrow Plan\n\n- (model did not produce a plan; edit this file)"
    return body + "\n"


def _fallback_prose(digest: dict[str, object]) -> str:
    """Deterministic retrospective used whenever no model answered."""
    lines = ["## Review\n"]
    productive = float(digest["productive_seconds"])
    distracting = float(digest["distracting_seconds"])
    effective = float(digest["attention"].get("effective_seconds", 0.0))  # type: ignore[union-attr]

    if effective <= 0:
        lines.append("- No effective attention was recorded for this day.")
    else:
        share = productive / effective * 100 if effective else 0
        lines.append(
            f"- {_fmt(productive)} of {_fmt(effective)} effective attention "
            f"({share:.0f}%) went to core productive or learning work."
        )
    if distracting > 0:
        lines.append(f"- {_fmt(distracting)} went to entertainment, gaming or social.")
    for entry in digest["attribution"][:3]:  # type: ignore[union-attr,index]
        lines.append(f"- {entry['title']}: {_fmt(entry['effective_seconds'])} effective.")
    for item in digest["stalled"][:3]:  # type: ignore[union-attr,index]
        lines.append(f"- No time went to '{item['title']}' today.")
    reasons = digest["distraction_reasons"]
    if reasons:
        top = max(reasons.items(), key=lambda kv: kv[1])  # type: ignore[union-attr]
        lines.append(f"- Most common interruption reason: {top[0]} (x{top[1]}).")

    lines.append("")
    lines.append("## Tomorrow Plan\n")
    planned = False
    note = digest["note"]
    if note and note["tomorrow"]:  # type: ignore[index]
        lines.append(f"- {note['tomorrow']}")  # type: ignore[index]
        planned = True
    for item in digest["stalled"][:2]:  # type: ignore[union-attr,index]
        lines.append(f"- Give '{item['title']}' a dedicated block.")
        planned = True
    for task in digest["open_tasks"][:3]:  # type: ignore[union-attr,index]
        estimate = f" (~{task['estimate_minutes']}m)" if task["estimate_minutes"] else ""
        lines.append(f"- {task['title']}{estimate}")
        planned = True
    if not planned:
        lines.append("- No open tasks or stalled deliverables recorded; add some to plan against.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# assembly and storage
# --------------------------------------------------------------------------
def render_markdown(digest: dict[str, object], prose: str) -> str:
    return (
        f"# Daily Review: {digest['workday_key']}\n\n"
        + render_objective_sections(digest).rstrip()
        + "\n\n"
        + prose.rstrip()
        + "\n\n"
        + render_tail_sections(digest).rstrip()
    ).rstrip() + "\n"


def generate_review(conn: Connection, config, workday_key: str) -> dict[str, object]:
    digest = build_digest(conn, config, workday_key)
    prose, generator, status, error = generate_prose(digest, config)
    markdown = render_markdown(digest, prose)
    save_review(conn, workday_key, markdown, generator, status, error)
    workday_service.set_review_status(conn, workday_key, workday_service.REVIEW_DONE)
    return {
        "workday_key": workday_key,
        "markdown": markdown,
        "generator": generator,
        "llm_status": status,
        "llm_error": error,
    }


def save_review(
    conn: Connection, workday_key: str, markdown: str, generator: str,
    llm_status: str, llm_error: str | None,
) -> None:
    now = datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO daily_reviews(id, workday_key, markdown, generator, llm_status,
                                  llm_error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workday_key) DO UPDATE SET
            markdown = excluded.markdown, generator = excluded.generator,
            llm_status = excluded.llm_status, llm_error = excluded.llm_error,
            updated_at = excluded.updated_at
        """,
        (uuid.uuid4().hex, workday_key, markdown, generator, llm_status, llm_error, now, now),
    )
    conn.commit()


def get_review(conn: Connection, workday_key: str) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT * FROM daily_reviews WHERE workday_key = ?", (workday_key,)
    ).fetchone()
    return dict(row) if row else None


def list_reviews(conn: Connection, limit: int = 30) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT workday_key, generator, llm_status, updated_at FROM daily_reviews "
        "ORDER BY workday_key DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def write_journal(config, workday_key: str, markdown: str):
    """Persist the review as a markdown journal file beside the database."""
    journal_dir = config.data_dir / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / f"{workday_key}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
