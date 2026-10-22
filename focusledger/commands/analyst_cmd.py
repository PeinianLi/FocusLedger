"""`focusledger analyst ...` — ask a local model about tracked time."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from focusledger.cli_support import ensure_initialized
from focusledger.db.connection import connect
from focusledger.services import analyst
from focusledger.services import llm as llm_service


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "analyst", help="Ask a local model questions about your tracked time."
    )
    sub = parser.add_subparsers(required=True)

    ask = sub.add_parser("ask", help="Answer one question with the local model and read-only tools.")
    ask.add_argument("question", help="For example: \"上周我的时间主要花在哪里？\"")
    ask.add_argument("--model", help="Ollama model (default: llm_analyst_model in config).")
    ask.add_argument("--trace", action="store_true", help="Print each tool call as it happens.")
    _model_options(ask)
    ask.set_defaults(func=cmd_ask)

    evaluate = sub.add_parser("eval", help="Run a fixed question set against one or more models.")
    evaluate.add_argument("--models", help="Comma-separated models (default: the analyst model).")
    evaluate.add_argument(
        "--questions", help='JSON file: [{"text": "...", "expect_tools": ["range_summary"]}].'
    )
    evaluate.add_argument("--out", help="Write every transcript to this JSON file.")
    _model_options(evaluate)
    evaluate.set_defaults(func=cmd_eval)

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
                on_tool_call=_print_call if args.trace else None, **_options(args),
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
