"""Helpers shared by the CLI entry point and the per-domain command modules.

Kept separate so command modules can be imported by ``cli`` without importing
it back.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from focusledger.config import AppConfig, ensure_config, load_config
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.services.activity import format_duration
from focusledger.services import workday as workday_service


def ensure_initialized() -> AppConfig:
    cfg = ensure_config()
    with connect(cfg.database_path) as conn:
        apply_migrations(conn)
    return load_config()


def add_date_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--today", action="store_true", help="Use today's local date.")
    group.add_argument("--date", dest="target_date", help="Use a date in YYYY-MM-DD format.")


def parse_target_date(args: argparse.Namespace) -> date:
    if getattr(args, "target_date", None):
        return date.fromisoformat(args.target_date)
    return date.today()


def resolve_workday_key(args: argparse.Namespace, config: AppConfig) -> str:
    """Which workday a command refers to.

    Defaults to the workday *now* belongs to rather than today's calendar date,
    so a command run at 01:00 still targets the day that is still in progress.
    """
    if getattr(args, "target_date", None):
        return str(date.fromisoformat(args.target_date))
    return workday_service.workday_key_for(datetime.now().astimezone(), config)


def fmt(seconds: float) -> str:
    return format_duration(float(seconds or 0.0))


def print_kv(pairs: list[tuple[str, object]], indent: str = "  ") -> None:
    for key, value in pairs:
        print(f"{indent}{key}: {value}")


def confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def prompt_text(label: str) -> str:
    try:
        return input(f"{label} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
