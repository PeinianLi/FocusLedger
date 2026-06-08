from __future__ import annotations

import argparse
from datetime import date, datetime

from focusledger.agent.heartbeat import latest_heartbeat
from focusledger.agent.runner import AgentRunOptions, request_stop, run_agent
from focusledger.config import config_path, ensure_config, load_config
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.logging_config import configure_logging
from focusledger.services.activity import format_duration, list_timeline, summarize_by_app


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="focusledger")
    subparsers = parser.add_subparsers(required=True)

    init_parser = subparsers.add_parser("init", help="Initialize local config and database.")
    init_parser.set_defaults(func=cmd_init)

    start_parser = subparsers.add_parser("start", help="Start the foreground collection agent.")
    start_parser.add_argument("--once", action="store_true", help="Collect one sample and exit.")
    start_parser.add_argument("--max-samples", type=int, default=None, help="Collect N samples and exit.")
    start_parser.set_defaults(func=cmd_start)

    stop_parser = subparsers.add_parser("stop", help="Ask the foreground agent to stop.")
    stop_parser.set_defaults(func=cmd_stop)

    status_parser = subparsers.add_parser("status", help="Show latest agent heartbeat.")
    status_parser.set_defaults(func=cmd_status)

    db_parser = subparsers.add_parser("db", help="Database helpers.")
    db_subparsers = db_parser.add_subparsers(required=True)
    db_path_parser = db_subparsers.add_parser("path", help="Print database path.")
    db_path_parser.set_defaults(func=cmd_db_path)

    config_parser = subparsers.add_parser("config", help="Config helpers.")
    config_subparsers = config_parser.add_subparsers(required=True)
    config_path_parser = config_subparsers.add_parser("path", help="Print config path.")
    config_path_parser.set_defaults(func=cmd_config_path)

    timeline_parser = subparsers.add_parser("timeline", help="Show activity timeline.")
    add_date_args(timeline_parser)
    timeline_parser.set_defaults(func=cmd_timeline)

    summary_parser = subparsers.add_parser("summary", help="Show activity summary.")
    add_date_args(summary_parser)
    summary_parser.add_argument("--app", action="store_true", help="Summarize by app/process.")
    summary_parser.set_defaults(func=cmd_summary)

    return parser


def add_date_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--today", action="store_true", help="Use today's local date.")
    group.add_argument("--date", dest="target_date", help="Use a date in YYYY-MM-DD format.")


def cmd_init(args: argparse.Namespace) -> None:
    cfg = ensure_config()
    configure_logging(cfg)
    with connect(cfg.database_path) as conn:
        apply_migrations(conn)
    print(f"Initialized FocusLedger data directory: {cfg.data_dir}")
    print(f"Config: {config_path(cfg.data_dir)}")
    print(f"Database: {cfg.database_path}")


def cmd_start(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    configure_logging(cfg)
    options = AgentRunOptions(once=args.once, max_samples=args.max_samples)
    print("Starting FocusLedger agent. Press Ctrl+C to stop.")
    try:
        run_agent(cfg, options)
    except KeyboardInterrupt:
        print("Stopping FocusLedger agent.")


def cmd_stop(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    request_stop(cfg)
    print("Stop requested. The foreground agent will exit after its next poll.")


def cmd_status(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        heartbeat = latest_heartbeat(conn)
    if not heartbeat:
        print("No agent heartbeat recorded yet.")
        return
    print("Latest heartbeat")
    print(f"  status: {heartbeat['status']}")
    print(f"  timestamp: {heartbeat['timestamp']}")
    print(f"  pid: {heartbeat['pid']}")
    print(f"  device_id: {heartbeat['device_id']}")
    if heartbeat.get("message"):
        print(f"  message: {heartbeat['message']}")


def cmd_db_path(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    print(cfg.database_path)


def cmd_config_path(args: argparse.Namespace) -> None:
    cfg = ensure_config()
    print(config_path(cfg.data_dir))


def cmd_timeline(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    day = parse_target_date(args)
    with connect(cfg.database_path) as conn:
        rows = list_timeline(conn, day)
    if not rows:
        print(f"No activity segments for {day}.")
        return
    for row in rows:
        start = datetime.fromisoformat(str(row["started_at"])).strftime("%H:%M:%S")
        end = datetime.fromisoformat(str(row["ended_at"])).strftime("%H:%M:%S")
        duration = format_duration(row["duration_seconds"])
        process = row["process_name"] or "(unknown)"
        title = row["window_title"] or ""
        print(f"{start} - {end}  {duration:>10}  {process}  {title}")


def cmd_summary(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    day = parse_target_date(args)
    with connect(cfg.database_path) as conn:
        rows = summarize_by_app(conn, day)
    if not rows:
        print(f"No activity segments for {day}.")
        return
    print(f"App summary for {day}")
    for row in rows:
        duration = format_duration(row["total_seconds"])
        process = row["process_name"]
        count = row["segment_count"]
        print(f"{duration:>10}  {count:>3} segments  {process}")


def ensure_initialized():
    cfg = ensure_config()
    with connect(cfg.database_path) as conn:
        apply_migrations(conn)
    return load_config()


def parse_target_date(args: argparse.Namespace) -> date:
    if getattr(args, "target_date", None):
        return date.fromisoformat(args.target_date)
    return date.today()
