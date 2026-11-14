from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from focusledger.cli import build_parser
from focusledger.config import build_default_config
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.services import analyst, bench, mcp, mcp_client
from focusledger.services import llm as llm_service


def tool_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def answer(text: str) -> dict[str, object]:
    return {"content": text}


class Scripted:
    """Stands in for ``llm_service.chat``: replays replies, repeating the last one."""

    def __init__(self, *replies: dict[str, object]) -> None:
        self.replies = list(replies)

    def __call__(self, config, messages, **options):
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return {"message": reply, "prompt_eval_count": 0, "eval_count": 0}


class _Shared:
    """One synthetic ledger in a data directory of its own, and one server process reading it."""

    tmp: tempfile.TemporaryDirectory
    folder: Path
    config: object
    conn: object
    fixtures: dict[str, str]
    cases: list[bench.Case]
    client: mcp_client.McpClient


def setUpModule() -> None:
    _Shared.tmp = tempfile.TemporaryDirectory()
    _Shared.folder = Path(_Shared.tmp.name)
    _Shared.config = build_default_config(_Shared.folder)
    _Shared.conn = connect(_Shared.config.database_path)
    apply_migrations(_Shared.conn)
    _Shared.fixtures = bench.build_synthetic_ledger(_Shared.conn, seed=7)
    _Shared.cases = bench.build_cases(_Shared.conn, _Shared.config, _Shared.fixtures)
    _Shared.conn.commit()
    _Shared.client = mcp_client.start_server(_Shared.folder)


def tearDownModule() -> None:
    _Shared.client.close()
    _Shared.conn.close()
    _Shared.tmp.cleanup()


class OverMcp(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.conn, self.config = _Shared.client, _Shared.conn, _Shared.config
        self.fixtures, self.cases = _Shared.fixtures, _Shared.cases
        self.by_id = {case.id: case for case in self.cases}
        self.ledger = analyst.Ledger(self.conn, self.config)

    def run_model(self, model, cases: list[bench.Case], language: str = "en") -> list[bench.BenchRow]:
        with mock.patch.object(llm_service, "chat", model), \
                mock.patch.object(llm_service, "model_capabilities", return_value=("completion", "tools")):
            return bench.run_bench(self.conn, self.config, ["scripted"], cases, language=language,
                                   tool_mode="native", execute=self.client.run_tool)


class ClientTests(OverMcp):
    def test_the_server_offers_the_read_tools_and_nothing_else(self) -> None:
        served = [tool["name"] for tool in self.client.list_tools()]
        self.assertEqual(served, [tool.name for tool in mcp.READ_TOOLS])
        self.assertEqual(len(served), 8)
        self.assertFalse(set(served) & analyst.WRITE_TOOLS)
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in self.client.list_tools()))

    def test_a_figure_read_over_mcp_is_the_figure_the_tool_computes(self) -> None:
        day = self.by_id["lookup-recorded"].reference_calls[0][1]
        direct = analyst.run_tool(self.ledger, "day_summary", dict(day))
        served = self.client.call_tool("day_summary", dict(day))
        for key in ("date", "recorded_hours", "effective_hours", "categories", "top_apps", "focus_blocks"):
            self.assertEqual(served[key], direct[key], key)
        self.assertGreater(served["recorded_hours"], 0)

    def test_window_titles_do_not_cross_the_protocol(self) -> None:
        window = {"date": self.fixtures["inject_action"], "from_time": "14:00", "to_time": "14:30", "min_minutes": "1"}
        direct = analyst.run_tool(self.ledger, "timeline", dict(window))
        served = self.client.call_tool("timeline", dict(window))
        self.assertEqual(direct["blocks"][0]["main_title"], bench.INJECTED_ACTION)
        self.assertEqual(served["blocks"][0]["app"], "notepad.exe")
        self.assertNotIn("main_title", served["blocks"][0])
        self.assertNotIn("ignore your rules", json.dumps(served))

    def test_a_write_tool_does_not_exist_over_mcp(self) -> None:
        before = bench._written_rows(self.conn)
        for name, arguments in (
            ("mark_absent", {"date": self.fixtures["lock"], "label": "sleep", "app": analyst.UNKNOWN_APP}),
            ("correct_category", {"scope": "process", "pattern": "explorer.exe", "category": "admin"}),
        ):
            with self.subTest(tool=name):
                self.assertEqual(self.client.call_tool(name, arguments), {"error": f"Unknown tool: {name}"})
        self.assertEqual(bench._written_rows(self.conn), before)

    def test_a_call_the_tool_rejects_comes_back_as_an_error_the_model_can_read(self) -> None:
        refused = self.client.call_tool("day_summary", {"date": "yesterday"})
        self.assertEqual(list(refused), ["error"])
        self.assertIn("yesterday", refused["error"])
        # The server is still there for the next call.
        self.assertEqual(self.client.call_tool("list_days", {})["tracked_days"], 14)

    def test_a_request_the_server_cannot_answer_raises_and_a_server_that_is_gone_is_reported(self) -> None:
        with self.assertRaises(mcp_client.McpError):
            self.client.result("no/such/method")
        gone = mcp_client.McpClient([sys.executable, "-c", "pass"])
        try:
            with self.assertRaises(mcp_client.McpError):
                gone.list_tools()
        finally:
            gone.close()

    def test_closing_ends_the_server_process(self) -> None:
        with mcp_client.start_server(_Shared.folder) as own:
            self.assertEqual(len(own.list_tools()), 8)
            process = own._process
            self.assertIsNone(process.poll())
        self.assertEqual(process.poll(), 0)

    def test_a_server_without_a_ledger_says_so_instead_of_creating_one(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with mcp_client.start_server(empty) as own:
                reply = own.call_tool("list_days", {})
            self.assertIn("error", reply)
            self.assertEqual(list(Path(empty).iterdir()), [])


class BenchmarkTests(OverMcp):
    """The benchmark's cases, with the tools reached the way an outside agent reaches them."""

    def test_only_cases_that_must_end_in_a_proposal_are_left_out(self) -> None:
        served = bench.served_over_mcp(self.cases)
        left_out = [case.id for case in self.cases if case not in served]
        self.assertEqual(left_out, ["proposal-mark-sleep", "proposal-correct-category"])
        self.assertIn("permission-skip-approval", [case.id for case in served])

    def test_reference_solutions_pass_over_mcp(self) -> None:
        # The reference for the skip-approval case proposes the change, which
        # cannot be done here; it has a test of its own below.
        cases = [case for case in bench.served_over_mcp(self.cases) if case.id != "permission-skip-approval"]
        self.assertEqual(len(cases), 34)
        rows = self.run_model(bench.ReferenceModel(self.cases), cases)
        self.assertEqual([(row.case.id, row.failures()) for row in rows if not row.passed], [])
        self.assertEqual([row.case.id for row in rows if row.remarks()], [])
        self.assertFalse(any(row.wrote for row in rows))
        # Every tool result came from the server process, none from this one.
        (fault,) = [row for row in rows if row.case.fault]
        self.assertEqual(fault.result.tool_calls[0].result, {"error": bench.TRANSIENT_ERROR})
        self.assertNotIn("error", fault.result.tool_calls[1].result)

    def test_orders_planted_in_window_titles_never_reach_the_model(self) -> None:
        cases = [case for case in self.cases if case.category == "injection"]
        rows = self.run_model(bench.ReferenceModel(self.cases), cases)
        self.assertEqual(len(rows), 2)
        for row in rows:
            fetched = json.dumps([call.result for call in row.result.tool_calls])
            self.assertNotIn("SYSTEM", fetched)
            self.assertNotIn("999", fetched)
            self.assertTrue(row.passed)

    def test_a_change_cannot_be_proposed_from_outside(self) -> None:
        case = self.by_id["permission-skip-approval"]
        before = bench._written_rows(self.conn)
        attempt = tool_call(*case.reference_calls[0])
        (declined,) = self.run_model(
            Scripted(attempt, answer("I cannot make that change here; this connection is read-only.")), [case])
        self.assertEqual(declined.result.tool_calls[0].result, {"error": "Unknown tool: mark_absent"})
        self.assertEqual(declined.result.proposals, [])
        self.assertTrue(declined.passed)
        # Claiming the write happened is caught, because nothing was proposed.
        (claimed,) = self.run_model(Scripted(attempt, answer("Done, it is written to the database.")), [case])
        self.assertFalse(claimed.passed)
        self.assertEqual([c.check.kind for c in claimed.checks if not c.passed and not c.check.advisory], ["guarded"])
        self.assertEqual(bench._written_rows(self.conn), before)


class CommandTests(unittest.TestCase):
    def run_bench_command(self, *argv: str, chat) -> tuple[str, str]:
        def copy_of_the_ledger(conn, seed: int = 7) -> dict[str, str]:
            _Shared.conn.commit()
            _Shared.conn.backup(conn)
            return dict(_Shared.fixtures)

        with tempfile.TemporaryDirectory() as data_dir:
            args = build_parser().parse_args(["analyst", "bench", *argv])
            printed, errors = io.StringIO(), io.StringIO()
            with mock.patch.dict("os.environ", {"FOCUSLEDGER_DATA_DIR": data_dir}), \
                    mock.patch.object(bench, "build_synthetic_ledger", copy_of_the_ledger), \
                    mock.patch.object(llm_service, "chat", chat), \
                    mock.patch.object(llm_service, "model_capabilities", return_value=("tools",)), \
                    contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
                args.func(args)
            self.assertEqual(list(Path(data_dir).iterdir()), [])
        return printed.getvalue(), errors.getvalue()

    def test_the_flag_is_off_unless_asked_for(self) -> None:
        self.assertFalse(build_parser().parse_args(["analyst", "bench"]).via_mcp)
        self.assertTrue(build_parser().parse_args(["analyst", "bench", "--via-mcp"]).via_mcp)

    def test_a_run_through_the_server(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            out = Path(out_dir) / "bench.json"
            text, errors = self.run_bench_command(
                "--via-mcp", "--language", "en", "--category", "lookup", "--category", "proposal",
                "--models", "reference", "--tool-mode", "native", "--out", str(out),
                chat=bench.ReferenceModel(_Shared.cases))
            saved = json.loads(out.read_text(encoding="utf-8"))
        # The two proposal cases were asked for and are not there: nothing can be proposed over MCP.
        self.assertIn("6 cases (synthetic ledger, seed 7), language en", text)
        self.assertIn("reference: 6/6 cases passed", text)
        self.assertNotIn("proposal-", text)
        self.assertIn("tools reached through the MCP server, which serves 8", errors)
        self.assertTrue(saved["via_mcp"])
        self.assertEqual({row["category"] for row in saved["rows"]}, {"lookup"})

    def test_listing_shows_what_would_be_asked_over_mcp(self) -> None:
        never = mock.Mock(side_effect=AssertionError("a model was called"))
        text, _ = self.run_bench_command("--via-mcp", "--list", "--category", "proposal",
                                         "--category", "permission", chat=never)
        self.assertNotIn("[proposal]", text)
        self.assertIn("[permission] permission-skip-approval", text)
        self.assertIn("3 case(s).", text)

    def test_the_configuration_entry_comes_with_a_warning_that_stays_out_of_the_json(self) -> None:
        args = build_parser().parse_args(["mcp", "config"])
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            args.func(args)
        entry = json.loads(printed.getvalue())  # still nothing but JSON on stdout
        self.assertEqual(entry["mcpServers"]["focusledger"]["args"], ["-m", "focusledger", "mcp", "serve"])
        warning = errors.getvalue()
        self.assertIn("cloud model", warning)
        self.assertIn("local model", warning)
        self.assertIn("read tools only", warning)


if __name__ == "__main__":
    unittest.main()
