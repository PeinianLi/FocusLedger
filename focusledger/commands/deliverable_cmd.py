"""`focusledger deliverable|task|distraction ...` — Phase 6 and Phase 7."""

from __future__ import annotations

import argparse

from focusledger.cli_support import (
    add_date_args,
    ensure_initialized,
    fmt,
    resolve_workday_key,
)
from focusledger.db.connection import connect
from focusledger.services import deliverables as dl
from focusledger.services.activity import list_attention_segments, list_timeline
from focusledger.services.alerts import REASONS, label_distraction, list_distractions
from focusledger.services.attention import AttentionPolicy
from focusledger.services.classify import (
    attach_attention_weight,
    classify_segments,
    load_rule_context,
)


def register(subparsers) -> None:
    _register_deliverable(subparsers)
    _register_task(subparsers)
    _register_distraction(subparsers)


# --------------------------------------------------------------------------
def _register_deliverable(subparsers) -> None:
    parser = subparsers.add_parser("deliverable", help="Manage deliverables (Phase 6).")
    sub = parser.add_subparsers(required=True)

    add = sub.add_parser("add", help="Create a deliverable.")
    add.add_argument("title")
    add.add_argument("--priority", type=int, default=2, choices=[1, 2, 3], help="1 high, 3 low.")
    add.add_argument("--due", dest="due_date", default=None, help="YYYY-MM-DD")
    add.add_argument(
        "--keywords", default=None,
        help="Comma-separated terms matched against app names and window titles.",
    )
    add.set_defaults(func=cmd_add)

    listing = sub.add_parser("list", help="List deliverables and today's time on each.")
    listing.add_argument("--all", action="store_true", help="Include archived.")
    add_date_args(listing)
    listing.set_defaults(func=cmd_list)

    done = sub.add_parser("done", help="Mark a deliverable done.")
    done.add_argument("deliverable")
    done.set_defaults(func=cmd_done)

    archive = sub.add_parser("archive", help="Archive a deliverable.")
    archive.add_argument("deliverable")
    archive.set_defaults(func=cmd_archive)

    edit = sub.add_parser("update", help="Change a deliverable's fields.")
    edit.add_argument("deliverable")
    edit.add_argument("--title", default=None)
    edit.add_argument("--priority", type=int, default=None, choices=[1, 2, 3])
    edit.add_argument("--due", dest="due_date", default=None)
    edit.add_argument("--keywords", default=None)
    edit.set_defaults(func=cmd_update)


def _resolve(conn, needle: str):
    found = dl.find_deliverable(conn, needle)
    if not found:
        raise SystemExit(f"error: no deliverable matching '{needle}'")
    return found


def cmd_add(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        item = dl.add_deliverable(conn, args.title, args.priority, args.due_date, args.keywords)
    print(f"Added deliverable {str(item['id'])[:8]}  {item['title']}")
    if not args.keywords:
        print("  Tip: --keywords lets activity be matched to it automatically.")


def cmd_list(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    from focusledger.cli_support import parse_target_date

    day = parse_target_date(args)
    with connect(cfg.database_path) as conn:
        items = dl.list_deliverables(conn, include_archived=args.all)
        segments = list_timeline(conn, day)
        classified = classify_segments(segments, load_rule_context(conn))
        attach_attention_weight(
            classified, list_attention_segments(conn, day, AttentionPolicy.from_config(cfg))
        )
        attribution = {str(e["id"]): e for e in dl.attribute_time(conn, classified)}
        tasks = dl.list_tasks(conn)

    if not items:
        print("No deliverables yet.  focusledger deliverable add \"Ship MVP\" --keywords focusledger")
        return

    print(f"Deliverables (time shown for {day})")
    print(f"{'ID':<10} {'PRI':<4} {'STATUS':<9} {'TIME':>9} {'EFFECTIVE':>10}  TITLE")
    for item in items:
        entry = attribution.get(str(item["id"]))
        seconds = entry["seconds"] if entry else 0
        effective = entry["effective_seconds"] if entry else 0
        due = f"  (due {item['due_date']})" if item["due_date"] else ""
        print(
            f"{str(item['id'])[:8]:<10} {dl.PRIORITY_LABELS.get(int(item['priority']), '?'):<4} "
            f"{str(item['status']):<9} {fmt(seconds):>9} {fmt(effective):>10}  {item['title']}{due}"
        )
        for task in [t for t in tasks if t["deliverable_id"] == item["id"]]:
            mark = "x" if task["status"] == dl.TASK_DONE else " "
            blocker = f"  blocked: {task['blocker']}" if task["blocker"] else ""
            print(f"           [{mark}] {str(task['id'])[:8]}  {task['title']}{blocker}")


def cmd_done(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        item = _resolve(conn, args.deliverable)
        dl.update_deliverable(conn, str(item["id"]), status=dl.STATUS_DONE)
    print(f"Marked done: {item['title']}")


def cmd_archive(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        item = _resolve(conn, args.deliverable)
        dl.update_deliverable(conn, str(item["id"]), status=dl.STATUS_ARCHIVED)
    print(f"Archived: {item['title']}")


def cmd_update(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        item = _resolve(conn, args.deliverable)
        updated = dl.update_deliverable(
            conn, str(item["id"]), title=args.title, priority=args.priority,
            due_date=args.due_date, match_keywords=args.keywords,
        )
    print(f"Updated: {updated['title']}")  # type: ignore[index]


# --------------------------------------------------------------------------
def _register_task(subparsers) -> None:
    parser = subparsers.add_parser("task", help="Manage tasks under deliverables (Phase 6).")
    sub = parser.add_subparsers(required=True)

    add = sub.add_parser("add", help="Create a task.")
    add.add_argument("title")
    add.add_argument("--deliverable", default=None, help="Deliverable id prefix or title fragment.")
    add.add_argument("--estimate", type=int, default=None, help="Estimated minutes.")
    add.set_defaults(func=cmd_task_add)

    listing = sub.add_parser("list", help="List tasks.")
    listing.add_argument("--open", action="store_true", help="Hide completed tasks.")
    listing.set_defaults(func=cmd_task_list)

    done = sub.add_parser("done", help="Mark a task done.")
    done.add_argument("task")
    done.set_defaults(func=cmd_task_done)

    block = sub.add_parser("block", help="Record what is blocking a task.")
    block.add_argument("task")
    block.add_argument("blocker")
    block.set_defaults(func=cmd_task_block)


def cmd_task_add(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        deliverable_id = None
        if args.deliverable:
            deliverable_id = str(_resolve(conn, args.deliverable)["id"])
        task = dl.add_task(conn, args.title, deliverable_id, args.estimate)
    print(f"Added task {str(task['id'])[:8]}  {task['title']}")


def cmd_task_list(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        tasks = dl.list_tasks(conn, include_done=not args.open)
        titles = {str(d["id"]): str(d["title"]) for d in dl.list_deliverables(conn, True)}
    if not tasks:
        print("No tasks.")
        return
    for task in tasks:
        mark = "x" if task["status"] == dl.TASK_DONE else " "
        parent = titles.get(str(task["deliverable_id"]), "-")
        estimate = f"  ~{task['estimate_minutes']}m" if task["estimate_minutes"] else ""
        blocker = f"  blocked: {task['blocker']}" if task["blocker"] else ""
        print(f"[{mark}] {str(task['id'])[:8]}  {task['title']}{estimate}  ({parent}){blocker}")


def cmd_task_done(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        task = dl.find_task(conn, args.task)
        if not task:
            raise SystemExit(f"error: no task matching '{args.task}'")
        dl.set_task_status(conn, str(task["id"]), dl.TASK_DONE)
    print(f"Done: {task['title']}")


def cmd_task_block(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        task = dl.find_task(conn, args.task)
        if not task:
            raise SystemExit(f"error: no task matching '{args.task}'")
        dl.set_task_status(conn, str(task["id"]), dl.TASK_BLOCKED, args.blocker)
    print(f"Blocked: {task['title']} — {args.blocker}")


# --------------------------------------------------------------------------
def _register_distraction(subparsers) -> None:
    parser = subparsers.add_parser("distraction", help="Review distraction alerts (Phase 7).")
    sub = parser.add_subparsers(required=True)

    listing = sub.add_parser("list", help="List alerts for a workday.")
    add_date_args(listing)
    listing.add_argument("--unlabeled", action="store_true", help="Only ones without a reason.")
    listing.set_defaults(func=cmd_distraction_list)

    label = sub.add_parser("label", help="Say why an interruption happened.")
    label.add_argument("event", help="Event id prefix.")
    label.add_argument("reason", choices=[key for key, _ in REASONS])
    label.set_defaults(func=cmd_distraction_label)

    reasons = sub.add_parser("reasons", help="List the available reason labels.")
    reasons.set_defaults(func=cmd_distraction_reasons)


def cmd_distraction_list(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    key = resolve_workday_key(args, cfg)
    with connect(cfg.database_path) as conn:
        events = list_distractions(conn, key, unlabeled_only=args.unlabeled)
    if not events:
        print(f"No distraction alerts for {key}.")
        return
    for event in events:
        when = str(event["detected_at"])[11:19]
        reason = event["reason"] or "(unlabeled)"
        print(
            f"{str(event['id'])[:8]}  {when}  {str(event['kind']):<14} "
            f"{fmt(event['duration_seconds']):>9}  {event['process_name'] or '-'}  {reason}"
        )
    print()
    print("Label one with:  focusledger distraction label <id> procrastination")


def cmd_distraction_label(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        rows = [e for e in list_distractions(conn) if str(e["id"]).startswith(args.event)]
        if not rows:
            raise SystemExit(f"error: no distraction event starting with '{args.event}'")
        label_distraction(conn, str(rows[0]["id"]), args.reason)
    print(f"Labelled as {args.reason}.")


def cmd_distraction_reasons(args: argparse.Namespace) -> None:
    for key, label in REASONS:
        print(f"  {key:<20} {label}")
