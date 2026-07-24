"""`focusledger review ...` — Phase 8."""

from __future__ import annotations

import argparse

from focusledger.cli_support import (
    add_date_args,
    ensure_initialized,
    prompt_text,
    resolve_workday_key,
)
from focusledger.db.connection import connect
from focusledger.services import review as review_service
from focusledger.services import workday as wd
from focusledger.services.deliverables import get_daily_note, save_daily_note


def register(subparsers) -> None:
    parser = subparsers.add_parser("review", help="Daily review and tomorrow's plan (Phase 8).")
    sub = parser.add_subparsers(required=True)

    write = sub.add_parser("write", help="Record what actually moved today (asked before generating).")
    add_date_args(write)
    write.set_defaults(func=cmd_write)

    generate = sub.add_parser("generate", help="Generate the review markdown.")
    add_date_args(generate)
    generate.add_argument("--no-llm", action="store_true", help="Force the deterministic version.")
    generate.add_argument("--print", dest="do_print", action="store_true", help="Print the markdown.")
    generate.set_defaults(func=cmd_generate)

    regenerate = sub.add_parser("regenerate", help="Rebuild an existing review.")
    add_date_args(regenerate)
    regenerate.add_argument("--no-llm", action="store_true")
    regenerate.add_argument("--print", dest="do_print", action="store_true")
    regenerate.set_defaults(func=cmd_generate)

    show = sub.add_parser("show", help="Print a saved review.")
    add_date_args(show)
    show.set_defaults(func=cmd_show)

    listing = sub.add_parser("list", help="List saved reviews.")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=cmd_list)

    pending = sub.add_parser("pending", help="Workdays that still need a review.")
    pending.set_defaults(func=cmd_pending)


def cmd_write(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    key = resolve_workday_key(args, cfg)
    print(f"Daily note for {key}. Press Enter to keep the existing answer.\n")
    with connect(cfg.database_path) as conn:
        existing = get_daily_note(conn, key) or {}
        progress = prompt_text("What did you actually move forward today?") or str(
            existing.get("progress") or ""
        )
        blocked = prompt_text("What is still blocked?") or str(existing.get("blocked") or "")
        tomorrow = prompt_text("What should be prioritised tomorrow?") or str(
            existing.get("tomorrow") or ""
        )
        save_daily_note(conn, key, progress, blocked, tomorrow)
    print("\nSaved. Now run: focusledger review generate")


def cmd_generate(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    key = resolve_workday_key(args, cfg)
    if args.no_llm:
        cfg = type(cfg)(**{**cfg.__dict__, "llm_enabled": False})

    with connect(cfg.database_path) as conn:
        note = get_daily_note(conn, key)
        if not note:
            print("No daily note recorded for this day.")
            print("The review is much more useful with one: focusledger review write")
            print()
        result = review_service.generate_review(conn, cfg, key)

    path = review_service.write_journal(cfg, key, str(result["markdown"]))
    print(f"Review for {key} generated.")
    print(f"  generator: {result['generator']}")
    if result["llm_status"] == review_service.LLM_FAILED:
        print(f"  local model unavailable: {result['llm_error']}")
        print("  fell back to the deterministic review (the day is still fully recorded).")
        print("  set one up with:  focusledger llm status")
    print(f"  journal: {path}")
    if args.do_print:
        print()
        print(result["markdown"])


def cmd_show(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    key = resolve_workday_key(args, cfg)
    with connect(cfg.database_path) as conn:
        saved = review_service.get_review(conn, key)
    if not saved:
        raise SystemExit(f"No review saved for {key}. Run: focusledger review generate --date {key}")
    print(saved["markdown"])


def cmd_list(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        rows = review_service.list_reviews(conn, args.limit)
    if not rows:
        print("No reviews saved yet.")
        return
    print(f"{'DAY':<12} {'GENERATOR':<24} {'LLM':<8} UPDATED")
    for row in rows:
        print(
            f"{str(row['workday_key']):<12} {str(row['generator']):<24} "
            f"{str(row['llm_status']):<8} {str(row['updated_at'])[:19]}"
        )


def cmd_pending(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    key = resolve_workday_key(args, cfg)
    with connect(cfg.database_path) as conn:
        rows = wd.pending_reviews(conn, key)
    if not rows:
        print("No workdays are waiting for a review.")
        return
    print("Workdays still needing a review:")
    for row in rows:
        print(f"  {row['workday_key']}   focusledger review generate --date {row['workday_key']}")
