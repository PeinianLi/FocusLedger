"""`focusledger classify ...` — Phase 5."""

from __future__ import annotations

import argparse

from focusledger.cli_support import add_date_args, ensure_initialized, fmt, parse_target_date
from focusledger.db.connection import connect
from focusledger.services.activity import list_timeline
from focusledger.services import llm as llm_service
from focusledger.services.classify import (
    CATEGORIES,
    label_unknowns_with_llm,
    list_rules,
    remove_rule,
    CATEGORY_LABELS,
    SCOPES,
    add_correction,
    classify_segments,
    list_corrections,
    load_rule_context,
    remove_correction,
    summarize_categories,
    unknown_targets,
)


def register(subparsers) -> None:
    parser = subparsers.add_parser("classify", help="Activity classification (Phase 5).")
    sub = parser.add_subparsers(required=True)

    summary = sub.add_parser("summary", help="Time per category for a day.")
    add_date_args(summary)
    summary.set_defaults(func=cmd_summary)

    detail = sub.add_parser("timeline", help="Per-segment classification for a day.")
    add_date_args(detail)
    detail.add_argument("--category", default=None, help="Only show one category.")
    detail.set_defaults(func=cmd_timeline)

    unknown = sub.add_parser("unknown", help="List unclassified time worth labelling.")
    add_date_args(unknown)
    unknown.set_defaults(func=cmd_unknown)

    auto = sub.add_parser(
        "auto", help="Let the local model label whatever is still unknown (offline)."
    )
    add_date_args(auto)
    auto.add_argument("--dry-run", action="store_true", help="Show labels without saving them.")
    auto.add_argument("--limit", type=int, default=12, help="Max items to label in one pass.")
    auto.set_defaults(func=cmd_auto)

    rules = sub.add_parser("rules", help="List rules learned for this machine.")
    rules.set_defaults(func=cmd_rules)

    correct = sub.add_parser("correct", help="Override the category for an app, site or keyword.")
    correct.add_argument("scope", choices=sorted(SCOPES))
    correct.add_argument("pattern")
    correct.add_argument("category", choices=sorted(CATEGORIES))
    correct.set_defaults(func=cmd_correct)

    forget = sub.add_parser("uncorrect", help="Remove a manual override.")
    forget.add_argument("scope", choices=sorted(SCOPES))
    forget.add_argument("pattern")
    forget.set_defaults(func=cmd_uncorrect)

    rules = sub.add_parser("corrections", help="List manual overrides.")
    rules.set_defaults(func=cmd_corrections)

    cats = sub.add_parser("categories", help="List available categories.")
    cats.set_defaults(func=cmd_categories)


def _classified_for(args: argparse.Namespace):
    cfg = ensure_initialized()
    day = parse_target_date(args)
    with connect(cfg.database_path) as conn:
        segments = list_timeline(conn, day)
        context = load_rule_context(conn)
    return day, classify_segments(segments, context)


def cmd_summary(args: argparse.Namespace) -> None:
    day, classified = _classified_for(args)
    totals = summarize_categories(classified)
    if not totals:
        print(f"No activity recorded for {day}.")
        return
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    grand = sum(totals.values()) or 1.0
    print(f"Categories for {day}")
    print(f"{'CATEGORY':<20} {'TIME':>10} {'SHARE':>7}")
    for category, seconds in ranked:
        if seconds <= 0:
            continue
        print(f"{CATEGORY_LABELS.get(category, category):<20} {fmt(seconds):>10} "
              f"{seconds / grand * 100:>6.1f}%")


def cmd_timeline(args: argparse.Namespace) -> None:
    day, classified = _classified_for(args)
    rows = [c for c in classified if not args.category or c["category"] == args.category]
    if not rows:
        print(f"No matching activity for {day}.")
        return
    for row in rows:
        start = str(row["started_at"])[11:19]
        end = str(row["ended_at"])[11:19]
        site = f" [{row['site']}]" if row.get("site") else ""
        print(
            f"{start} - {end}  {fmt(row['duration_seconds']):>9}  "
            f"{CATEGORY_LABELS.get(str(row['category']), str(row['category'])):<18} "
            f"{row['process']}{site}  ({row['matched_by']})"
        )


def cmd_unknown(args: argparse.Namespace) -> None:
    day, classified = _classified_for(args)
    targets = unknown_targets(classified)
    if not targets:
        print(f"Everything tracked on {day} was classified.")
        return
    print(f"Unclassified time on {day}:")
    for entry in targets:
        sample = str(entry.get("sample_title") or "")[:44]
        print(f"  {fmt(entry['seconds']):>9}  {entry['scope']:<14} {entry['pattern']}")
        if sample:
            print(f"{'':>11}  e.g. {sample}")
    print()
    print("Label one with:")
    first = targets[0]
    print(f"  focusledger classify correct {first['scope']} \"{first['pattern']}\" productive_core")


def cmd_correct(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        add_correction(conn, cfg.device_id, args.scope, args.pattern, args.category)
    print(f"{args.scope} '{args.pattern}' will now classify as {args.category}.")
    print("This re-labels past days too, since classification is derived at query time.")


def cmd_uncorrect(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        removed = remove_correction(conn, args.scope, args.pattern)
    print("Removed." if removed else "No such correction.")


def cmd_corrections(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        rows = list_corrections(conn)
    if not rows:
        print("No manual corrections yet.")
        return
    for row in rows:
        print(f"  {row['scope']:<14} {str(row['pattern']):<32} -> {row['category']}")


def cmd_categories(args: argparse.Namespace) -> None:
    for category in CATEGORIES:
        print(f"  {category:<22} {CATEGORY_LABELS[category]}")


def cmd_auto(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    day, classified = _classified_for(args)
    targets = unknown_targets(classified)[: args.limit]
    if not targets:
        print(f"Nothing unknown on {day} — everything is already classified.")
        return

    print(f"Asking the local model to label {len(targets)} unknown item(s)...")
    with connect(cfg.database_path) as conn:
        try:
            labels = label_unknowns_with_llm(conn, cfg, targets, apply=not args.dry_run)
        except llm_service.LlmUnavailable as exc:
            print(f"error: local model unavailable — {exc}")
            print()
            print("Set one up with `focusledger llm status`, or label them yourself:")
            first = targets[0]
            print(f'  focusledger classify correct {first["scope"]} "{first["pattern"]}" productive_core')
            raise SystemExit(1)

    if not labels:
        print("The model did not return any usable labels. Nothing was changed.")
        return
    for label in labels:
        print(f"  {fmt(label['seconds']):>9}  {label['scope']:<14} {label['pattern']:<28} -> {label['category']}")
    if args.dry_run:
        print("\n(dry run — nothing saved)")
    else:
        print(f"\nSaved {len(labels)} rule(s). They re-label past days too.")
        print("Disagree with one?  focusledger classify correct <scope> <pattern> <category>")


def cmd_rules(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        rows = list_rules(conn)
    if not rows:
        print("No learned rules yet.  Try: focusledger classify auto --today")
        return
    for row in rows:
        note = f"  ({row['note']})" if row["note"] else ""
        print(f"  {str(row['match_type']):<14} {str(row['pattern']):<30} -> {row['category']}{note}")
