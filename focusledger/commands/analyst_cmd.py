"""`focusledger analyst ...` — ask a local model about tracked time."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from focusledger.cli_support import ensure_initialized
from focusledger.config import load_config
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.services import analyst, bench, changes
from focusledger.services import llm as llm_service


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "analyst", help="Ask a local model questions about your tracked time."
    )
    sub = parser.add_subparsers(required=True)

    ask = sub.add_parser("ask", help="Answer one question with the local model and read-only tools.")
    ask.add_argument("question", help="For example: \"上周我的时间主要花在哪里？\"")
    ask.add_argument("--model", help="Ollama model (default: llm_analyst_model in config).")
    ask.add_argument("--trace", action="store_true",
                     help="Print each tool call and the model's thinking as they happen.")
    ask.add_argument("--yes", action="store_true",
                     help="Apply changes the model proposes without asking.")
    _model_options(ask)
    ask.set_defaults(func=cmd_ask)

    history = sub.add_parser("changes", help="List changes made on your behalf.")
    history.set_defaults(func=cmd_changes)

    undo = sub.add_parser("undo", help="Undo a change by its id.")
    undo.add_argument("change_id")
    undo.set_defaults(func=cmd_undo)

    evaluate = sub.add_parser("eval", help="Run a fixed question set against one or more models.")
    evaluate.add_argument("--models", help="Comma-separated models (default: the analyst model).")
    evaluate.add_argument(
        "--questions", help='JSON file: [{"text": "...", "expect_tools": ["range_summary"]}].'
    )
    evaluate.add_argument("--out", help="Write every transcript to this JSON file.")
    _model_options(evaluate)
    evaluate.set_defaults(func=cmd_eval)

    benchmark = sub.add_parser(
        "bench",
        help="Grade models on questions with known answers, using a synthetic ledger.",
    )
    benchmark.add_argument("--models", help="Comma-separated models (default: the analyst model).")
    benchmark.add_argument("--language", choices=bench.LANGUAGES,
                           help="Language of the questions and answers (default: the configured one).")
    benchmark.add_argument("--seed", type=int, default=7,
                           help="Seed of the synthetic ledger (default 7). The same seed gives the same cases.")
    benchmark.add_argument("--category", action="append", choices=bench.CATEGORIES,
                           help="Only run this category; may be given more than once.")
    benchmark.add_argument("--limit", type=int, help="Only run the first N cases.")
    benchmark.add_argument("--repeat", type=int, default=1,
                           help="Run every case this many times, to see how often the outcome changes "
                           "(default 1).")
    benchmark.add_argument(
        "--real", action="store_true",
        help="Derive the cases from your own ledger instead of a synthetic one. Cases that need a "
        "planted situation are left out, and the results are not comparable between people.",
    )
    benchmark.add_argument("--list", action="store_true",
                           help="Print the cases with their checks and reference solutions; no model is run.")
    benchmark.add_argument("--out", help="Write every transcript and grade to this JSON file.")
    benchmark.add_argument(
        "--regrade", metavar="FILE",
        help="Grade the transcripts in a file written by --out again, with the current rules; no "
        "model is run. The seed, language and repeat count are taken from the file.",
    )
    _model_options(benchmark)
    benchmark.set_defaults(func=cmd_bench)

    tools = sub.add_parser("tools", help="List the tools the model can call.")
    tools.set_defaults(func=cmd_tools)

    tool = sub.add_parser("tool", help="Run one tool directly, without a model.")
    tool.add_argument("name", choices=sorted(analyst.TOOLS_BY_NAME))
    tool.add_argument("--args", default="{}", help="JSON arguments, e.g. '{\"date\": \"2026-08-07\"}'.")
    tool.set_defaults(func=cmd_tool)


def _model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--think", action=argparse.BooleanOptionalAction, default=None,
        help="Turn the model's thinking on or off (default: the model's own default).",
    )
    parser.add_argument(
        "--max-rounds", type=int, default=analyst.DEFAULT_ROUNDS,
        help=f"Most tool-calling rounds before the model must answer (default {analyst.DEFAULT_ROUNDS}).",
    )
    parser.add_argument(
        "--num-ctx", type=int, default=analyst.DEFAULT_NUM_CTX,
        help=f"Context window in tokens (default {analyst.DEFAULT_NUM_CTX}).",
    )
    parser.add_argument("--timeout", type=float, default=None, help="Seconds to wait per model reply.")
    parser.add_argument(
        "--tool-mode", choices=analyst.TOOL_MODES, default="auto",
        help="native: Ollama tool calls; text: the model writes JSON calls in its reply; "
        "auto (default): probe the model once and pick.",
    )


def _options(args: argparse.Namespace) -> dict[str, object]:
    return {
        "think": args.think,
        "max_rounds": args.max_rounds,
        "num_ctx": args.num_ctx,
        "timeout": args.timeout,
        "tool_mode": args.tool_mode,
    }


def cmd_ask(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        try:
            result = analyst.analyze(
                conn, cfg, args.question, model=args.model,
                on_tool_call=_print_call if args.trace else None,
                on_thinking=_print_thinking if args.trace else None,
                **_options(args),
            )
        except llm_service.LlmUnavailable as exc:
            raise SystemExit(f"error: {exc}")
    print(result.answer or "(the model gave no answer)")
    print()
    print(
        f"-- {result.model} ({result.tool_mode} tool calls): {len(result.tool_calls)} tool "
        f"call(s), {result.rounds} round(s), {result.seconds:.0f}s"
    )
    for correction in result.corrections:
        print(f"-- sent back to the model once: {correction}")
    if result.stop_reason == "round_limit":
        print(f"-- stopped after {args.max_rounds} tool rounds; the answer may be incomplete")
    if result.ungrounded_numbers:
        print(
            "-- warning: these numbers appear in no tool result, so the model may have "
            "computed or invented them: " + ", ".join(result.ungrounded_numbers)
        )
    for proposal in result.proposals:
        _confirm_and_apply(cfg, proposal, args.yes)


def _confirm_and_apply(cfg, proposal: changes.Proposal, assume_yes: bool) -> None:
    """Nothing the model proposes is written without a yes from the user."""
    print()
    print(f"Proposed change: {proposal.summary}")
    for key, value in proposal.preview.items():
        print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
    approved = assume_yes
    if not assume_yes:
        try:
            answer = input("Apply this change? [y/N] ") if sys.stdin.isatty() else None
        except (EOFError, KeyboardInterrupt):
            answer = None
        if answer is None:
            print("\nNot applied: no answer to read. Re-run with --yes to apply it.")
            return
        approved = answer.strip().lower() in ("y", "yes")
    if not approved:
        print("Not applied.")
        return
    with connect(cfg.database_path) as conn:
        change = changes.apply(conn, cfg, proposal, source="cli")
    print(f"Applied as change {change['id']}. Undo with: focusledger analyst undo {change['id']}")


def _print_thinking(thought: str) -> None:
    for line in thought.strip().splitlines():
        print(f"  | {line}", flush=True)


def cmd_changes(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        rows = changes.list_changes(conn, limit=30)
    if not rows:
        print("No changes have been made on your behalf.")
        return
    for row in rows:
        state = f"undone {row['undone_at']}" if row["undone_at"] else "active"
        print(f"{row['id']}  {row['created_at']}  {row['source']:<8} {state:<28} {row['summary']}")


def cmd_undo(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    with connect(cfg.database_path) as conn:
        try:
            change = changes.undo(conn, cfg, args.change_id)
        except changes.ChangeError as exc:
            raise SystemExit(f"error: {exc}")
    print(f"Undone: {change['summary']}")


def _print_call(call: analyst.ToolCall) -> None:
    arguments = json.dumps(call.arguments, ensure_ascii=False)
    if "error" in call.result:
        outcome = f"error: {call.result['error']}"
    else:
        outcome = f"{len(json.dumps(call.result, ensure_ascii=False))} chars"
    print(f"  > {call.name} {arguments}  ({call.seconds:.1f}s, {outcome})", flush=True)


def cmd_eval(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    models = [m.strip() for m in (args.models or analyst.analyst_model(cfg)).split(",") if m.strip()]
    questions = _load_questions(Path(args.questions)) if args.questions else analyst.DEFAULT_EVAL_QUESTIONS
    for index, question in enumerate(questions, start=1):
        print(f"Q{index}: {question.text}")
    print()
    print(f"{'model':<22} {'q':>2}  mode    answered  right_tool  tools  fixes  ungrounded  seconds")
    with connect(cfg.database_path) as conn:
        rows = analyst.run_eval(conn, cfg, models, questions, on_row=_print_row, **_options(args))

    print()
    print(f"{'model':<22} answered  right_tool  ungrounded  median_s")
    for model in models:
        own = [row for row in rows if row.model == model]
        seconds = [row.result.seconds for row in own if row.result]
        print(
            f"{model:<22} {sum(r.answered for r in own):>4}/{len(own):<4}"
            f"{sum(r.used_expected_tool for r in own):>6}/{len(own):<5}"
            f"{sum(len(r.result.ungrounded_numbers) for r in own if r.result):>8}"
            f"{statistics.median(seconds) if seconds else 0:>12.0f}"
        )
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                [
                    {
                        "model": row.model,
                        "question": row.question.text,
                        "expect_tools": list(row.question.expect_tools),
                        "error": row.error,
                        "result": row.result.to_json_dict() if row.result else None,
                    }
                    for row in rows
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nTranscripts written to {args.out}")


def _print_row(row: analyst.EvalRow) -> None:
    if row.result is None:
        print(f"{row.model:<22} {row.index:>2}  error: {row.error}", flush=True)
        return
    result = row.result
    print(
        f"{row.model:<22} {row.index:>2}  {result.tool_mode:<6}  "
        f"{'yes' if row.answered else 'no':<8}  "
        f"{'yes' if row.used_expected_tool else 'no':<10}  {len(result.tool_calls):>5}  "
        f"{len(result.corrections):>5}  {len(result.ungrounded_numbers):>10}  "
        f"{result.seconds:>7.0f}",
        flush=True,
    )


def _load_questions(path: Path) -> list[analyst.EvalQuestion]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [
            analyst.EvalQuestion(str(item["text"]), tuple(item.get("expect_tools") or ()))
            for item in raw
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"error: cannot read questions from {path}: {exc}")


def cmd_bench(args: argparse.Namespace) -> None:
    saved = _saved_run(args.regrade) if args.regrade else None
    if saved:
        # The transcripts answer the cases of one ledger, in one language.
        args.real = saved["seed"] is None
        args.seed = args.seed if args.real else int(saved["seed"])
        args.language = str(saved["language"])
        args.repeat = int(saved.get("repeat") or 1)
    cfg = ensure_initialized() if args.real else load_config()
    language = args.language or ("zh" if str(getattr(cfg, "language", "en")).startswith("zh") else "en")
    with tempfile.TemporaryDirectory() as scratch:
        if args.real:
            run_cfg = replace(cfg, language=language)
            fixtures = None
        else:
            # A ledger of its own, in a folder that is thrown away: the user's
            # data is neither read nor written. Only the model settings are kept.
            root = Path(scratch)
            run_cfg = replace(
                cfg, language=language, data_dir=root, device_id="bench",
                database_path=root / "bench.sqlite3", log_path=root / "bench.log",
            )
        with closing(connect(run_cfg.database_path)) as conn:
            if not args.real:
                apply_migrations(conn)
                print(f"Building the synthetic ledger (seed {args.seed}) ...", file=sys.stderr, flush=True)
                fixtures = bench.build_synthetic_ledger(conn, args.seed)
            cases = bench.build_cases(conn, run_cfg, fixtures)
            if args.category:
                cases = [case for case in cases if case.category in args.category]
            if args.limit is not None:
                cases = cases[: max(0, args.limit)]
            if not cases:
                raise SystemExit("error: no cases to run (the ledger has too little data, or the "
                                 "filters left nothing)")
            if args.list:
                for case in cases:
                    print(bench.describe(case, language))
                    print()
                print(f"{len(cases)} case(s).")
                return
            models = [m.strip() for m in (args.models or analyst.analyst_model(cfg)).split(",") if m.strip()]
            source = "your own ledger" if args.real else f"synthetic ledger, seed {args.seed}"
            print(f"{len(cases)} cases ({source}), language {language}")
            print()
            print(f"{'model':<20} {'case':<30} result  tools  fixes  seconds")
            if saved:
                rows, stale = bench.regrade(saved, cases)
                for row in rows:
                    _print_bench_row(row)
                if stale:
                    print(f"\n{len(stale)} transcript(s) not graded, their case is gone or asks something "
                          f"else now: {', '.join(stale)}")
            else:
                rows = bench.run_bench(conn, run_cfg, models, cases, language=language,
                                       repeat=args.repeat, on_row=_print_bench_row, **_options(args))

    summaries = bench.summarize(rows)
    for summary in summaries:
        print()
        _print_scorecard(summary)
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"language": language, "seed": None if args.real else args.seed, "repeat": args.repeat,
                 "summary": summaries, "rows": [row.to_json_dict() for row in rows]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nTranscripts written to {args.out}")


def _saved_run(path: str) -> dict[str, object]:
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(saved["rows"], list) or "seed" not in saved or "language" not in saved:
            raise ValueError("not a file written by analyst bench --out")
        return saved
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"error: cannot read the saved run {path}: {exc}")


def _print_scorecard(summary: dict[str, object]) -> None:
    """One model's totals, a line for each of the seven dimensions."""
    def frac(pair) -> str:
        return f"{pair[0]}/{pair[1]}"

    def score(part: dict[str, object]) -> str:
        return f"{part['passed']}/{part['checks']}"

    dims: dict[str, dict[str, object]] = summary["dimensions"]  # type: ignore[assignment]
    retrieval, evidence, process = dims["retrieval"], dims["evidence"], dims["process"]
    stability, cost, security = dims["stability"], dims["cost"], dims["security"]
    print(f"{summary['model']}: {summary['passed']}/{summary['cases']} cases passed "
          f"({summary['tool_mode']} tool calls)")
    print(f"  1 result     {score(dims['result']):>8}  figures, names and dates in the answer")
    print(f"  2 retrieval  {score(retrieval):>8}  right tool {frac(retrieval['right_tool'])}, "
          f"evidence fetched {frac(retrieval['evidence_fetched'])}")
    print(f"  3 evidence   {score(evidence):>8}  every number grounded {frac(evidence['grounded'])}, "
          f"key figure fetched and stated {frac(evidence['figure_fetched_and_stated'])}, "
          f"{evidence['ungrounded_numbers']} ungrounded number(s)")
    print(f"  4 process    {score(process):>8}  within call budget {frac(process['within_budget'])}, "
          f"no failed calls {frac(process['no_failed_calls'])}, "
          f"no rewrite needed {frac(process['no_rewrite_needed'])}, "
          f"stopped normally {frac(process['stopped_normally'])}")
    stable = (f"  5 stability  {stability['passed']}/{stability['cases']:<6}  "
              f"same question reworded {frac(stability['wordings'])}, "
              f"recovered from a failed call {frac(stability['recovered_from_failed_call'])}")
    if stability["same_outcome_every_run"]:
        stable += f", same outcome on every run {frac(stability['same_outcome_every_run'])}"
    print(stable)
    per_pass = cost["tokens_per_passed_case"]
    print(f"  6 cost       {cost['prompt_tokens_per_case']:.0f} prompt + {cost['output_tokens_per_case']:.0f} "
          f"output tokens per case, {'-' if per_pass is None else per_pass} tokens per passed case, "
          f"median {cost['median_seconds']} s, {cost['rounds_per_case']} model rounds and "
          f"{cost['tool_calls_per_case']} tool calls per case")
    print(f"  7 security   {score(security):>8}  proposal, injection and permission cases "
          f"{frac(security['cases'])}, {security['unasked_proposals']} unasked proposal(s), "
          f"{security['database_writes']} database write(s)")
    parts = ", ".join(f"{name} {done}/{total}" for name, (done, total) in summary["by_category"].items())  # type: ignore[union-attr]
    print(f"  by category: {parts}")
    if summary["errors"]:
        print(f"  {summary['errors']} case(s) could not be run (model unavailable)")
    if summary["wrote"]:
        print(f"  WARNING: the database changed during {summary['wrote']} case(s)")


def _print_bench_row(row: bench.BenchRow) -> None:
    if row.result is None:
        print(f"{row.model:<20} {row.case.id:<30} error: {row.error}", flush=True)
        return
    line = (
        f"{row.model:<20} {row.case.id:<30} {'pass' if row.passed else 'FAIL':<6}"
        f"{len(row.result.tool_calls):>6}{len(row.result.corrections):>7}{row.result.seconds:>9.0f}"
    )
    if not row.passed:
        line += "  " + "; ".join(row.failures())
    elif row.remarks():
        line += "  (" + "; ".join(row.remarks()) + ")"
    print(line, flush=True)


def cmd_tools(args: argparse.Namespace) -> None:
    for tool in analyst.TOOLS:
        print(f"{tool.name}")
        print(f"  {tool.description}")


def cmd_tool(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    try:
        arguments = json.loads(args.args)
    except ValueError as exc:
        raise SystemExit(f"error: --args is not valid JSON: {exc}")
    with connect(cfg.database_path) as conn:
        output = analyst.run_tool(analyst.Ledger(conn, cfg), args.name, arguments)
    print(json.dumps(output, ensure_ascii=False, indent=2))
