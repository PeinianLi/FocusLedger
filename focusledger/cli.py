from __future__ import annotations

import argparse
import webbrowser
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from focusledger.agent.heartbeat import latest_heartbeat
from focusledger.agent.runner import (
    AgentRunOptions,
    mark_agent_stopped,
    request_start,
    request_stop,
    run_agent,
)
from focusledger.config import AppConfig, config_path, ensure_config, load_config, save_config
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.logging_config import configure_logging
from focusledger.services.activity import (
    format_duration,
    list_attention_segments,
    list_timeline,
    summarize_attention,
    summarize_by_app,
)
from focusledger.services.attention import AttentionPolicy
from focusledger.services.dev import reset_database
from focusledger.services.privacy import redact_existing_private_browser_titles
from focusledger.cli_support import ensure_initialized as _ensure_initialized
from focusledger.server.app import create_server, serve_forever
from focusledger.services.report import build_report_data, render_report_html


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="focusledger")
    subparsers = parser.add_subparsers(required=True)

    init_parser = subparsers.add_parser("init", help="Initialize local config and database.")
    init_parser.set_defaults(func=cmd_init)

    start_parser = subparsers.add_parser("start", help="Start the collection agent.")
    start_parser.add_argument("--once", action="store_true", help="Collect one sample and exit.")
    start_parser.add_argument("--max-samples", type=int, default=None, help="Collect N samples and exit.")
    start_parser.add_argument("--background", action="store_true", help="Start the agent in the background.")
    start_parser.add_argument("--background-child", action="store_true", help=argparse.SUPPRESS)
    start_parser.set_defaults(func=cmd_start)

    stop_parser = subparsers.add_parser("stop", help="Ask the running agent to stop.")
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
    config_attention_parser = config_subparsers.add_parser(
        "attention",
        help="Show attention thresholds and weights.",
    )
    config_attention_parser.set_defaults(func=cmd_config_attention)
    config_set_parser = config_subparsers.add_parser("set", help="Update config values.")
    config_set_subparsers = config_set_parser.add_subparsers(required=True)
    poll_interval_parser = config_set_subparsers.add_parser(
        "poll-interval",
        help="Set foreground window sampling interval in seconds.",
    )
    poll_interval_parser.add_argument("seconds", type=int)
    poll_interval_parser.set_defaults(func=cmd_config_set_poll_interval)
    attention_thresholds_parser = config_set_subparsers.add_parser(
        "attention-thresholds",
        help="Set low/gap/interrupted/hard-idle thresholds in minutes.",
    )
    attention_thresholds_parser.add_argument("low_activity_minutes", type=float)
    attention_thresholds_parser.add_argument("attention_gap_minutes", type=float)
    attention_thresholds_parser.add_argument("interrupted_minutes", type=float)
    attention_thresholds_parser.add_argument("hard_idle_minutes", type=float)
    attention_thresholds_parser.set_defaults(func=cmd_config_set_attention_thresholds)
    attention_weights_parser = config_set_subparsers.add_parser(
        "attention-weights",
        help="Set active/low/gap/interrupted/hard-idle weights.",
    )
    attention_weights_parser.add_argument("active", type=float)
    attention_weights_parser.add_argument("low_activity", type=float)
    attention_weights_parser.add_argument("attention_gap", type=float)
    attention_weights_parser.add_argument("interrupted", type=float)
    attention_weights_parser.add_argument("hard_idle", type=float)
    attention_weights_parser.set_defaults(func=cmd_config_set_attention_weights)

    timeline_parser = subparsers.add_parser("timeline", help="Show activity timeline.")
    add_date_args(timeline_parser)
    timeline_parser.set_defaults(func=cmd_timeline)

    summary_parser = subparsers.add_parser("summary", help="Show activity summary.")
    add_date_args(summary_parser)
    summary_parser.add_argument("--app", action="store_true", help="Summarize by app/process.")
    summary_parser.set_defaults(func=cmd_summary)

    attention_parser = subparsers.add_parser(
        "attention", help="Show keyboard/mouse attention states and effective time."
    )
    add_date_args(attention_parser)
    attention_parser.add_argument(
        "--timeline",
        action="store_true",
        help="Show continuous attention-state segments.",
    )
    attention_parser.set_defaults(func=cmd_attention)

    report_parser = subparsers.add_parser(
        "report", help="Serve an interactive live report at http://127.0.0.1."
    )
    add_date_args(report_parser)
    report_parser.add_argument(
        "--port",
        dest="port",
        type=int,
        default=0,
        help="Port to listen on (default: an available port chosen by the OS).",
    )
    report_parser.add_argument(
        "--no-open",
        dest="no_open",
        action="store_true",
        help="Print the URL without opening a browser.",
    )
    report_parser.add_argument(
        "--export",
        dest="export",
        default=None,
        help="Write a standalone snapshot HTML file instead of serving.",
    )
    report_parser.set_defaults(func=cmd_report)

    privacy_parser = subparsers.add_parser("privacy", help="Privacy maintenance helpers.")
    privacy_subparsers = privacy_parser.add_subparsers(required=True)
    redact_parser = privacy_subparsers.add_parser(
        "redact-private-browser-titles",
        help="Redact private/incognito browser window titles already stored in the local database.",
    )
    redact_parser.set_defaults(func=cmd_privacy_redact_browser_titles)

    from focusledger.commands import REGISTRARS
    for register in REGISTRARS:
        register(subparsers)

    dev_parser = subparsers.add_parser("dev", help="Development and testing helpers.")
    dev_subparsers = dev_parser.add_subparsers(required=True)
    reset_parser = dev_subparsers.add_parser("reset-db", help="Delete and recreate the local SQLite database.")
    reset_parser.add_argument("--yes", action="store_true", help="Confirm destructive database reset.")
    reset_parser.set_defaults(func=cmd_dev_reset_db)

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

    if args.background and not args.background_child:
        if args.once or args.max_samples is not None:
            raise SystemExit("--background cannot be used with --once or --max-samples")
        launch_background_agent(cfg)
        return

    request_start(cfg)
    options = AgentRunOptions(once=args.once, max_samples=args.max_samples)
    print("Starting FocusLedger agent. Press Ctrl+C to stop.")
    print(f"Database: {cfg.database_path}")
    print(f"Poll interval: {cfg.poll_interval_seconds}s")
    try:
        run_agent(cfg, options)
    except KeyboardInterrupt:
        mark_agent_stopped(cfg, "interrupted by user")
        print("Stopping FocusLedger agent.")


def cmd_stop(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    request_stop(cfg)
    print("Stop requested. The agent will exit after its next poll.")


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
    age_seconds = heartbeat_age_seconds(str(heartbeat["timestamp"]))
    stale_after = max(cfg.poll_interval_seconds * 3, 30)
    print(f"  age_seconds: {age_seconds:.1f}")
    if heartbeat["status"] == "running" and age_seconds > stale_after:
        print(f"  stale: true (no heartbeat within {stale_after}s)")
    else:
        print("  stale: false")
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


def cmd_config_set_poll_interval(args: argparse.Namespace) -> None:
    if args.seconds < 1:
        raise SystemExit("poll interval must be at least 1 second")
    cfg = ensure_config()
    updated = replace(cfg, poll_interval_seconds=args.seconds)
    save_config(updated)
    print(f"poll_interval_seconds set to {args.seconds}")


def cmd_config_attention(args: argparse.Namespace) -> None:
    cfg = ensure_config()
    policy = AttentionPolicy.from_config(cfg)
    policy.validate()
    print("Attention thresholds")
    print(f"  low_activity_after: {format_minutes(policy.low_activity_after_seconds)}")
    print(f"  attention_gap_after: {format_minutes(policy.attention_gap_after_seconds)}")
    print(f"  interrupted_after: {format_minutes(policy.interrupted_after_seconds)}")
    print(f"  hard_idle_after: {format_minutes(policy.hard_idle_after_seconds)}")
    print("Attention weights")
    print(f"  active: {policy.active_weight}")
    print(f"  low_activity: {policy.low_activity_weight}")
    print(f"  attention_gap: {policy.attention_gap_weight}")
    print(f"  interrupted: {policy.interrupted_weight}")
    print(f"  hard_idle: {policy.hard_idle_weight}")


def cmd_config_set_attention_thresholds(args: argparse.Namespace) -> None:
    cfg = ensure_config()
    values = (
        args.low_activity_minutes,
        args.attention_gap_minutes,
        args.interrupted_minutes,
        args.hard_idle_minutes,
    )
    seconds = tuple(int(value * 60) for value in values)
    updated = replace(
        cfg,
        low_activity_after_seconds=seconds[0],
        attention_gap_after_seconds=seconds[1],
        interrupted_after_seconds=seconds[2],
        hard_idle_after_seconds=seconds[3],
    )
    AttentionPolicy.from_config(updated).validate()
    save_config(updated)
    print("Attention thresholds updated.")
    cmd_config_attention(args)


def cmd_config_set_attention_weights(args: argparse.Namespace) -> None:
    cfg = ensure_config()
    updated = replace(
        cfg,
        attention_active_weight=args.active,
        attention_low_activity_weight=args.low_activity,
        attention_gap_weight=args.attention_gap,
        attention_interrupted_weight=args.interrupted,
        attention_hard_idle_weight=args.hard_idle,
    )
    AttentionPolicy.from_config(updated).validate()
    save_config(updated)
    print("Attention weights updated.")
    cmd_config_attention(args)


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


def cmd_attention(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    day = parse_target_date(args)
    policy = AttentionPolicy.from_config(cfg)
    with connect(cfg.database_path) as conn:
        data = summarize_attention(
            conn,
            day,
            cfg.poll_interval_seconds,
            policy,
        )
        segments = list_attention_segments(conn, day, policy)
    if not data["per_app"]:
        print(f"No activity samples for {day}.")
        return
    print(f"Attention summary for {day} (poll interval {cfg.poll_interval_seconds}s)")
    print(f"  active: {format_duration(data['active_seconds'])}")
    print(f"  low_activity: {format_duration(data['low_activity_seconds'])}")
    print(f"  attention_gap: {format_duration(data['attention_gap_seconds'])}")
    print(f"  interrupted: {format_duration(data['interrupted_seconds'])}")
    print(f"  hard_idle: {format_duration(data['hard_idle_seconds'])}")
    print(f"  unknown: {format_duration(data['unknown_seconds'])}")
    print(f"  raw: {format_duration(data['raw_seconds'])}")
    print(f"  effective: {format_duration(data['effective_seconds'])}")
    print()
    print("Per app (raw / effective / active / low_activity)")
    for row in data["per_app"]:
        raw = format_duration(row["raw_seconds"])
        effective = format_duration(row["effective_seconds"])
        active = format_duration(row["active"])
        low_activity = format_duration(row["low_activity"])
        print(
            f"  {raw:>10} raw / {effective:>10} effective / "
            f"{active:>10} active / {low_activity:>10} low   {row['process_name']}"
        )

    compact_timeline = render_attention_timeline(segments)
    if compact_timeline:
        print()
        print("Attention timeline")
        for line in compact_timeline:
            print(line)

    if args.timeline and segments:
        print()
        print("Attention segments")
        for row in segments:
            start = datetime.fromisoformat(str(row["started_at"])).strftime("%H:%M:%S")
            end = datetime.fromisoformat(str(row["ended_at"])).strftime("%H:%M:%S")
            duration = format_duration(row["duration_seconds"])
            state = row["attention_state"]
            state_weight = row["attention_weight"]
            max_idle = format_duration(row["max_idle_seconds"])
            print(
                f"  {start} - {end}  {duration:>10}  {state:<14} "
                f"weight={state_weight:g} max_idle={max_idle}"
            )


def render_attention_timeline(
    segments: list[dict[str, object]],
    *,
    width: int = 72,
    charset: str = "auto",
) -> list[str]:
    if not segments:
        return []
    symbols = attention_timeline_symbols(charset)

    parsed = []
    for row in segments:
        started_at = datetime.fromisoformat(str(row["started_at"]))
        ended_at = datetime.fromisoformat(str(row["ended_at"]))
        if ended_at < started_at:
            ended_at = started_at
        parsed.append(
            {
                "started_at": started_at,
                "ended_at": ended_at,
                "state": str(row["attention_state"] or "unknown"),
                "weight": float(row["attention_weight"] or 0.0),
            }
        )

    start = min(row["started_at"] for row in parsed)
    end = max(row["ended_at"] for row in parsed)
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0:
        row = parsed[0]
        char = attention_timeline_char(row["state"], symbols)
        label = start.strftime("%H:%M:%S")
        return [
            f"  range: {label}",
            f"  [{char}]",
            f"  {attention_timeline_legend(symbols)}",
        ]

    slots = max(1, width)
    chars = []
    for index in range(slots):
        slot_start = start + (end - start) * (index / slots)
        slot_end = start + (end - start) * ((index + 1) / slots)
        overlapping = [
            row
            for row in parsed
            if row["started_at"] < slot_end and row["ended_at"] >= slot_start
        ]
        if not overlapping:
            chars.append(" ")
            continue
        selected = min(overlapping, key=lambda row: row["weight"])
        chars.append(attention_timeline_char(selected["state"], symbols))

    start_label = start.strftime("%H:%M")
    end_label = end.strftime("%H:%M")
    return [
        f"  range: {start_label} - {end_label}",
        f"  [{''.join(chars)}]",
        f"  {attention_timeline_legend(symbols)}",
    ]


def attention_timeline_char(state: str, symbols: dict[str, str]) -> str:
    return symbols.get(state, symbols["unknown"])


def attention_timeline_symbols(charset: str = "auto") -> dict[str, str]:
    unicode_symbols = {
        "active": "\u2588",
        "low_activity": "\u2592",
        "attention_gap": "\u2591",
        "interrupted": "!",
        "hard_idle": "\u00b7",
        "unknown": "?",
    }
    ascii_symbols = {
        "active": "A",
        "low_activity": "L",
        "attention_gap": "G",
        "interrupted": "!",
        "hard_idle": "H",
        "unknown": "?",
    }
    if charset == "unicode":
        return unicode_symbols
    if charset == "ascii":
        return ascii_symbols
    sample = "".join(unicode_symbols.values())
    encoding = sys.stdout.encoding or "utf-8"
    try:
        sample.encode(encoding)
    except UnicodeEncodeError:
        return ascii_symbols
    return unicode_symbols


def attention_timeline_legend(symbols: dict[str, str]) -> str:
    return (
        f"legend: {symbols['active']} active | "
        f"{symbols['low_activity']} low_activity | "
        f"{symbols['attention_gap']} attention_gap | "
        f"{symbols['interrupted']} interrupted | "
        f"{symbols['hard_idle']} hard_idle | "
        f"{symbols['unknown']} unknown"
    )


def cmd_report(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    day = parse_target_date(args)

    if args.export:
        _export_report(cfg, day, Path(args.export).expanduser())
        return

    server = create_server(cfg, day, port=args.port)
    url = server.url
    print("FocusLedger report server running.")
    print(f"  url: {url}")
    print(f"  date: {day} (live — follows today as it is collected)")
    print("  press Ctrl+C to stop")
    if not args.no_open:
        webbrowser.open(url)
    serve_forever(server)
    print("\nReport server stopped.")


def _export_report(cfg, day, output_path: Path) -> None:
    policy = AttentionPolicy.from_config(cfg)
    with connect(cfg.database_path) as conn:
        data = build_report_data(conn, day, cfg.poll_interval_seconds, policy, cfg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report_html(data), encoding="utf-8")
    segments = len(data["attention"]["segments"])  # type: ignore[index]
    print(f"Exported FocusLedger snapshot for {day}.")
    print(f"  segments: {segments}")
    print(f"  open: file://{output_path.resolve()}")


def cmd_privacy_redact_browser_titles(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        counts = redact_existing_private_browser_titles(conn)
    print("Redacted private/incognito browser window titles.")
    print(f"  raw_activity_events: {counts['raw_activity_events']}")
    print(f"  activity_segments: {counts['activity_segments']}")


def cmd_dev_reset_db(args: argparse.Namespace) -> None:
    cfg = ensure_config()
    if not args.yes:
        print("This will delete and recreate the local FocusLedger SQLite database.")
        print(f"Database: {cfg.database_path}")
        print("Run again with --yes to confirm.")
        return

    deleted = reset_database(cfg.database_path)
    print("Reset FocusLedger database.")
    if deleted:
        print("Deleted:")
        for path in deleted:
            print(f"  {path}")
    else:
        print("Deleted: none")
    print(f"Recreated: {cfg.database_path}")


def ensure_initialized():
    return _ensure_initialized()


def launch_background_agent(cfg: AppConfig) -> None:
    request_start(cfg)
    project_root = Path(__file__).resolve().parents[1]
    stdout_path = cfg.data_dir / "agent.stdout.log"
    stderr_path = cfg.data_dir / "agent.stderr.log"
    command = [
        sys.executable,
        "-u",
        "-m",
        "focusledger",
        "start",
        "--background-child",
    ]

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(project_root)
        if not existing_pythonpath
        else f"{project_root}{os.pathsep}{existing_pythonpath}"
    )

    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    stdout_file = stdout_path.open("a", encoding="utf-8")
    stderr_file = stderr_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
    finally:
        stdout_file.close()
        stderr_file.close()

    print("Started FocusLedger agent in the background.")
    print(f"  pid: {process.pid}")
    print(f"  database: {cfg.database_path}")
    print(f"  poll_interval: {cfg.poll_interval_seconds}s")
    print(f"  stdout_log: {stdout_path}")
    print(f"  stderr_log: {stderr_path}")


def parse_target_date(args: argparse.Namespace) -> date:
    if getattr(args, "target_date", None):
        return date.fromisoformat(args.target_date)
    return date.today()


def heartbeat_age_seconds(timestamp: str) -> float:
    heartbeat_time = datetime.fromisoformat(timestamp)
    if heartbeat_time.tzinfo is None:
        heartbeat_time = heartbeat_time.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (now - heartbeat_time.astimezone(timezone.utc)).total_seconds())


def format_minutes(seconds: int) -> str:
    minutes = seconds / 60
    return f"{minutes:g} min"
