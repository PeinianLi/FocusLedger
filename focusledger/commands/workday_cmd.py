"""`focusledger workday ...` — Phase 3."""

from __future__ import annotations

import argparse

from focusledger.cli_support import (
    add_date_args,
    ensure_initialized,
    fmt,
    print_kv,
    resolve_workday_key,
)
from focusledger.db.connection import connect
from focusledger.services import workday as wd


def register(subparsers) -> None:
    parser = subparsers.add_parser("workday", help="Manage the workday session (Phase 3).")
    sub = parser.add_subparsers(required=True)

    start = sub.add_parser("start", help="Begin today's workday.")
    start.set_defaults(func=cmd_start)

    pause = sub.add_parser("pause", help="Pause tracking (breaks are not counted as work).")
    pause.add_argument("--reason", default=None)
    pause.set_defaults(func=cmd_pause)

    resume = sub.add_parser("resume", help="Resume after a pause.")
    resume.set_defaults(func=cmd_resume)

    offwork = sub.add_parser("offwork", help="End the workday.")
    offwork.set_defaults(func=cmd_offwork)

    overtime = sub.add_parser("overtime", help="Continue past offwork time as overtime.")
    overtime.set_defaults(func=cmd_overtime)

    status = sub.add_parser("status", help="Show the current workday and derived totals.")
    add_date_args(status)
    status.set_defaults(func=cmd_status)

    listing = sub.add_parser("list", help="List recent workdays.")
    listing.add_argument("--limit", type=int, default=14)
    listing.set_defaults(func=cmd_list)

    holiday = sub.add_parser("mark-holiday", help="Mark a day as a holiday (no review prompts).")
    add_date_args(holiday)
    holiday.set_defaults(func=cmd_mark_holiday)

    vacation = sub.add_parser("mark-vacation", help="Mark a day as vacation (no review prompts).")
    add_date_args(vacation)
    vacation.set_defaults(func=cmd_mark_vacation)


def _run(action) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        try:
            session = action(conn, cfg)
        except wd.WorkdayError as exc:
            raise SystemExit(f"error: {exc}")
    print(f"Workday {session['workday_key']}: {session['status']}")


def cmd_start(args: argparse.Namespace) -> None:
    _run(lambda conn, cfg: wd.start_workday(conn, cfg))


def cmd_pause(args: argparse.Namespace) -> None:
    _run(lambda conn, cfg: wd.pause_workday(conn, reason=args.reason))


def cmd_resume(args: argparse.Namespace) -> None:
    _run(lambda conn, cfg: wd.resume_workday(conn))


def cmd_overtime(args: argparse.Namespace) -> None:
    _run(lambda conn, cfg: wd.mark_overtime(conn))


def cmd_offwork(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        try:
            session = wd.end_workday(conn)
        except wd.WorkdayError as exc:
            raise SystemExit(f"error: {exc}")
        view = wd.describe(conn, cfg, str(session["workday_key"]))
    print(f"Workday {session['workday_key']} ended.")
    if view:
        print_kv([
            ("worked", fmt(view.worked_seconds)),
            ("paused", fmt(view.paused_seconds)),
            ("missing data", fmt(view.missing_data_seconds)),
        ])
    print()
    print("Next: `focusledger review write` to record progress, then `focusledger review generate`.")


def cmd_status(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    key = resolve_workday_key(args, cfg)
    with connect(cfg.database_path) as conn:
        view = wd.describe(conn, cfg, key)
        pending = wd.pending_reviews(conn, key)
    if not view:
        print(f"No workday recorded for {key}.")
        print("Start one with: focusledger workday start")
        return

    session = view.session
    print(f"Workday {key}")
    print_kv([
        ("status", session["status"]),
        ("started", str(session["started_at"])[11:19]),
        ("ended", str(session["ended_at"])[11:19] if session["ended_at"] else "-"),
        ("worked", fmt(view.worked_seconds)),
        ("paused", fmt(view.paused_seconds)),
        ("overtime", "yes" if session["is_overtime"] else "no"),
        ("review", session["review_status"]),
    ])
    if view.missing_intervals:
        print(f"  missing data: {fmt(view.missing_data_seconds)}")
        for interval in view.missing_intervals:
            print(
                f"    {str(interval['started_at'])[11:16]} - {str(interval['ended_at'])[11:16]}"
                f"  ({fmt(interval['duration_seconds'])})"
            )
    if pending:
        print()
        print(f"{len(pending)} earlier workday(s) still need a review:")
        for row in pending:
            print(f"  {row['workday_key']}  (focusledger review generate --date {row['workday_key']})")


def cmd_list(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        sessions = wd.list_sessions(conn, args.limit)
        rows = []
        for session in sessions:
            view = wd.describe(conn, cfg, str(session["workday_key"]))
            rows.append((session, view))
    if not rows:
        print("No workdays recorded yet.")
        return
    print(f"{'DAY':<12} {'STATUS':<10} {'WORKED':>10} {'MISSING':>10}  REVIEW")
    for session, view in rows:
        print(
            f"{str(session['workday_key']):<12} {str(session['status']):<10} "
            f"{fmt(view.worked_seconds if view else 0):>10} "
            f"{fmt(view.missing_data_seconds if view else 0):>10}  {session['review_status']}"
        )


def _mark(args: argparse.Namespace, status: str) -> None:
    cfg = ensure_initialized()
    key = resolve_workday_key(args, cfg)
    with connect(cfg.database_path) as conn:
        try:
            wd.mark_day(conn, cfg, key, status)
        except wd.WorkdayError as exc:
            raise SystemExit(f"error: {exc}")
    print(f"{key} marked as {status}.")


def cmd_mark_holiday(args: argparse.Namespace) -> None:
    _mark(args, wd.HOLIDAY)


def cmd_mark_vacation(args: argparse.Namespace) -> None:
    _mark(args, wd.VACATION)
