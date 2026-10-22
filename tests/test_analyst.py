from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from focusledger.config import build_default_config
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.services import analyst, changes
from focusledger.services import llm as llm_service
from focusledger.services.activity import list_attention_segments, record_activity_snapshot
from focusledger.services.classify import add_correction

STEAM_GAME = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Terraria\\Terraria.exe"


class AnalystFixture(unittest.TestCase):
    """Two tracked days with a gap between them, sampled every 10 seconds."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = build_default_config(Path(self._tmp.name))
        self.conn = connect(self.config.database_path)
        apply_migrations(self.conn)
        morning = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
        self.record(morning, 180, "Code.exe", "main.py - proj", "C:\\Apps\\Code.exe")
        self.record(
            morning + timedelta(minutes=30), 60, "chrome.exe",
            "Funny cats - YouTube - Google Chrome", "C:\\Apps\\chrome.exe",
        )
        self.record(
            datetime(2026, 7, 22, 21, 0, tzinfo=timezone.utc), 120, "Terraria.exe",
            "Terraria", STEAM_GAME,
        )

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def record(self, start: datetime, samples: int, process: str, title: str, path: str) -> None:
        for index in range(samples):
            record_activity_snapshot(
                self.conn,
                device_id="dev",
                snapshot=ActiveWindowSnapshot(
                    captured_at=start + timedelta(seconds=10 * index),
                    process_id=1,
                    process_name=process,
                    executable_path=path,
                    window_title=title,
                    window_handle="1",
                ),
                segment_gap_seconds=15,
                idle_seconds=0.0,
            )

    def tool(self, name: str, **arguments: object) -> dict[str, object]:
        return analyst.run_tool(analyst.Ledger(self.conn, self.config), name, arguments)


class ToolTests(AnalystFixture):
    def test_list_days_indexes_dates_and_gaps(self) -> None:
        index = self.tool("list_days")
        self.assertEqual(index["first_date"], "2026-07-20")
        self.assertEqual(index["last_date"], "2026-07-22")
        self.assertEqual(index["untracked_ranges"], ["2026-07-21..2026-07-21"])
        self.assertEqual(index["last_7_tracked_days"], {"start": "2026-07-20", "end": "2026-07-22"})

    def test_day_summary_reports_apps_categories_and_focus(self) -> None:
        day = self.tool("day_summary", date="2026-07-20")
        self.assertTrue(day["attention_tracked"])
        self.assertEqual(day["recorded_hours"], 0.7)
        self.assertEqual(day["top_apps"][0]["app"], "Code.exe")
        self.assertEqual(day["top_apps"][0]["category"], "productive_core")
        self.assertEqual(day["first_activity"], "09:00")
        self.assertEqual(day["focus_blocks"], [
            {"start": "09:00", "end": "09:39", "minutes": 40, "main_app": "Code.exe"}
        ])

    def test_range_and_comparison_return_computed_changes(self) -> None:
        summary = self.tool("range_summary", start="2026-07-20", end="2026-07-22")
        self.assertEqual(summary["tracked_days"], 2)
        self.assertEqual(summary["untracked_ranges"], ["2026-07-21..2026-07-21"])
        self.assertIn("gaming", [row["category"] for row in summary["categories"]])

        compared = self.tool(
            "compare_ranges",
            a_start="2026-07-20", a_end="2026-07-20", b_start="2026-07-22", b_end="2026-07-22",
        )
        gaming = compared["change_b_minus_a"]["category_hours_per_day"]["gaming"]
        self.assertEqual(gaming, {"hours": 0.3})

    def test_app_usage_timeline_and_hourly_profile(self) -> None:
        usage = self.tool("app_usage", query="CODE")
        self.assertEqual(usage["matches"][0]["name"], "Code.exe")
        self.assertEqual(usage["days_used"], 1)

        timeline = self.tool("timeline", date="2026-07-20", min_minutes="5")
        self.assertEqual(
            [(b["start"], b["app"]) for b in timeline["blocks"]],
            [("09:00", "Code.exe"), ("09:30", "chrome.exe")],
        )
        self.assertEqual(timeline["blocks"][1]["category"], "entertainment")

        profile = self.tool("hourly_profile")
        self.assertEqual(profile["days_averaged"], 2)
        self.assertEqual(profile["most_focused_hours"][0], "09:00")

    def test_bad_arguments_come_back_as_errors(self) -> None:
        self.assertIn("no tracked data", self.tool("day_summary", date="2026-07-21")["error"])
        self.assertIn("required", self.tool("range_summary")["error"])
        self.assertIn("unknown tool", self.tool("delete_everything")["error"])


# --------------------------------------------------------------------------
def tool_call(name: str, arguments: object) -> dict[str, object]:
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def answer(text: str) -> dict[str, object]:
    return {"role": "assistant", "content": text}


class ScriptedModel:
    """Stands in for ``llm_service.chat`` and replays a fixed conversation."""

    def __init__(self, *replies: dict[str, object]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, object]] = []

    def __call__(self, config, messages, **options):
        self.calls.append({"messages": [dict(m) for m in messages], **options})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return {"message": reply, "prompt_eval_count": 100, "eval_count": 20}


class LoopTests(AnalystFixture):
    def run_with(self, model: ScriptedModel, capabilities=("completion", "tools"), **options):
        options.setdefault("tool_mode", "native")
        with mock.patch.object(llm_service, "chat", model), \
                mock.patch.object(llm_service, "model_capabilities", return_value=capabilities):
            return analyst.analyze(self.conn, self.config, "7月20日我做了什么？", **options)

    def test_text_mode_parses_json_calls_from_the_reply(self) -> None:
        model = ScriptedModel(
            answer('<think>need data</think>\n```json\n{"tool": "day_summary", '
                   '"arguments": {"date": "2026-07-20"}}\n```'),
            answer("记录了 0.7 小时，主要在 Code.exe。"),
        )
        result = self.run_with(model, tool_mode="text")

        self.assertEqual(result.tool_mode, "text")
        self.assertEqual([c.name for c in result.tool_calls], ["day_summary"])
        self.assertIsNone(model.calls[0]["tools"])
        self.assertIn('{"tool": "<tool name>"', model.calls[0]["messages"][0]["content"])
        self.assertTrue(model.calls[1]["messages"][-1]["content"].startswith("Result of day_summary"))
        self.assertEqual(result.answer, "记录了 0.7 小时，主要在 Code.exe。")

    def test_auto_mode_falls_back_to_text_when_the_probe_gets_prose(self) -> None:
        analyst._NATIVE_TOOL_CALLS.clear()
        model = ScriptedModel(
            answer("I cannot ping anything."),  # the probe
            answer('{"tool": "list_days", "arguments": {}}'),
            answer("数据到 2026-07-22 为止。"),
        )
        result = self.run_with(model, tool_mode="auto")

        self.assertEqual(result.tool_mode, "text")
        self.assertEqual(model.calls[0]["tools"][0]["function"]["name"], "ping")
        self.assertEqual([c.name for c in result.tool_calls], ["list_days"])
        analyst._NATIVE_TOOL_CALLS.clear()

    def test_auto_mode_uses_text_for_models_without_tool_support(self) -> None:
        model = ScriptedModel(
            answer('{"tool": "list_days", "arguments": {}}'),
            answer("数据到 2026-07-22 为止。"),
        )
        result = self.run_with(model, capabilities=("completion",), tool_mode="auto")
        self.assertEqual(result.tool_mode, "text")

    def test_tool_results_feed_the_answer(self) -> None:
        model = ScriptedModel(
            tool_call("day_summary", {"date": "2026-07-20"}),
            answer("7月20日记录了 0.7 小时，主要在 Code.exe。"),
        )
        result = self.run_with(model)

        self.assertEqual(result.stop_reason, "answered")
        self.assertEqual([c.name for c in result.tool_calls], ["day_summary"])
        self.assertEqual(result.rounds, 2)
        self.assertEqual((result.ungrounded_numbers, result.corrections), ([], []))
        tool_messages = [m for m in model.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(tool_messages[0]["tool_name"], "day_summary")
        self.assertIn('"recorded_hours": 0.7', tool_messages[0]["content"])
        self.assertIn("Simplified Chinese", model.calls[0]["messages"][0]["content"])

    def test_answer_with_invented_numbers_is_sent_back_once_fixed(self) -> None:
        model = ScriptedModel(
            tool_call("day_summary", {"date": "2026-07-20"}),
            answer("记录了 0.7 小时，另外还玩了 123.4 小时游戏。"),
            answer("记录了 0.7 小时，主要在 Code.exe。"),
        )
        result = self.run_with(model)

        self.assertEqual(result.answer, "记录了 0.7 小时，主要在 Code.exe。")
        self.assertEqual(result.corrections, ["numbers not from any tool: 123.4"])
        self.assertEqual(result.ungrounded_numbers, [])
        self.assertIn("123.4", model.calls[2]["messages"][-1]["content"])

    def test_numbers_still_invented_after_corrections_are_flagged(self) -> None:
        model = ScriptedModel(
            tool_call("day_summary", {"date": "2026-07-20"}),
            answer("另外还玩了 123.4 小时游戏。"),
        )
        result = self.run_with(model)

        self.assertEqual(len(result.corrections), analyst.MAX_CORRECTIONS)
        self.assertEqual(result.ungrounded_numbers, ["123.4"])
        self.assertEqual(result.stop_reason, "answered")

    def test_answer_without_any_tool_call_is_sent_back(self) -> None:
        model = ScriptedModel(
            answer("你那天写了 4.2 小时代码。"),
            tool_call("day_summary", {"date": "2026-07-20"}),
            answer("主要在 Code.exe，记录了 0.7 小时。"),
        )
        result = self.run_with(model)

        self.assertEqual(result.corrections, ["answered without calling a tool"])
        self.assertEqual([c.name for c in result.tool_calls], ["day_summary"])
        self.assertEqual(result.answer, "主要在 Code.exe，记录了 0.7 小时。")

    def test_json_string_arguments_and_unknown_tools_are_handled(self) -> None:
        model = ScriptedModel(
            tool_call("drop_tables", {}),
            tool_call("day_summary", '{"date": "2026-07-22"}'),
            answer("那天主要在玩 Terraria.exe。"),
        )
        result = self.run_with(model)

        self.assertIn("unknown tool", result.tool_calls[0].result["error"])
        self.assertEqual(result.tool_calls[1].result["top_apps"][0]["category"], "gaming")
        self.assertEqual(result.answer, "那天主要在玩 Terraria.exe。")

    def test_round_limit_forces_an_answer_without_tools(self) -> None:
        model = ScriptedModel(
            tool_call("list_days", {}),
            tool_call("list_days", {}),
            answer("<think>enough</think>数据到 2026-07-22 为止。"),
        )
        result = self.run_with(model, max_rounds=2)

        self.assertEqual(result.stop_reason, "round_limit")
        self.assertEqual(result.answer, "数据到 2026-07-22 为止。")
        self.assertIsNotNone(model.calls[0]["tools"])
        self.assertIsNone(model.calls[-1]["tools"])

    def test_native_mode_is_refused_for_models_without_tool_support(self) -> None:
        with self.assertRaises(llm_service.LlmUnavailable):
            self.run_with(ScriptedModel(answer("hi")), capabilities=("completion",))

    def test_eval_checks_the_expected_tool(self) -> None:
        model = ScriptedModel(
            tool_call("range_summary", {"start": "2026-07-20", "end": "2026-07-22"}),
            answer("主要在 Code.exe。"),
        )
        questions = [analyst.EvalQuestion("最近时间花在哪？", ("range_summary",))]
        with mock.patch.object(llm_service, "chat", model), \
                mock.patch.object(llm_service, "model_capabilities", return_value=("tools",)):
            rows = analyst.run_eval(
                self.conn, self.config, ["fake:1b"], questions, tool_mode="native"
            )

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].answered)
        self.assertTrue(rows[0].used_expected_tool)


class ChangeTests(AnalystFixture):
    def annotations(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM time_annotations").fetchone()[0]

    def test_marking_an_app_absent_waits_for_approval_and_can_be_undone(self) -> None:
        ledger = analyst.Ledger(self.conn, self.config)
        before = analyst.run_tool(ledger, "day_summary", {"date": "2026-07-20"})
        proposal = analyst.run_tool(
            ledger, "mark_absent", {"date": "2026-07-20", "label": "sleep", "app": "chrome.exe"}
        )
        self.assertEqual(proposal["status"], "awaiting_user_approval")
        self.assertEqual(proposal["preview"]["stretches"], 1)
        self.assertEqual(proposal["preview"]["marked_hours"], 0.2)
        self.assertEqual(proposal["preview"]["effective_hours_before"], 0.7)
        self.assertEqual(proposal["preview"]["effective_hours_after"], 0.5)
        self.assertEqual(self.annotations(), 0)  # proposing writes nothing

        change = changes.apply(self.conn, self.config, ledger.proposals[0])
        after = self.tool("day_summary", date="2026-07-20")
        self.assertEqual(self.annotations(), 1)
        self.assertEqual(after["effective_hours"], 0.5)
        self.assertEqual(
            {row["app"]: row["category"] for row in after["top_apps"]}["chrome.exe"], "idle"
        )
        self.assertEqual(after["marked_absent"][0]["label"], "sleep")
        states = {s.get("annotation") for s in list_attention_segments(
            self.conn, datetime(2026, 7, 20, tzinfo=timezone.utc).date())}
        self.assertIn("sleep", states)

        changes.undo(self.conn, self.config, str(change["id"]))
        restored = self.tool("day_summary", date="2026-07-20")
        self.assertEqual(self.annotations(), 0)
        self.assertEqual(restored["effective_hours"], before["effective_hours"])
        self.assertNotIn("marked_absent", restored)
        with self.assertRaises(changes.ChangeError):
            changes.undo(self.conn, self.config, str(change["id"]))

    def test_a_clock_range_can_run_past_midnight(self) -> None:
        ledger = analyst.Ledger(self.conn, self.config)
        proposal = analyst.run_tool(ledger, "mark_absent", {
            "date": "2026-07-22", "label": "sleep", "start_time": "21:10", "end_time": "07:00",
        })
        start, end = ledger.proposals[0].arguments["ranges"][0]
        self.assertEqual((start, end), ("2026-07-22T21:10:00+00:00", "2026-07-23T07:00:00+00:00"))
        self.assertEqual(proposal["preview"]["effective_hours_after"], 0.2)

        bad = analyst.run_tool(ledger, "mark_absent", {"date": "2026-07-22", "label": "nap"})
        self.assertIn("label must be one of", bad["error"])

    def test_a_category_change_restores_the_previous_correction_on_undo(self) -> None:
        add_correction(self.conn, "dev", "process", "code.exe", "admin")
        ledger = analyst.Ledger(self.conn, self.config)
        proposal = analyst.run_tool(ledger, "correct_category", {
            "scope": "process", "pattern": "Code.exe", "category": "productive_core",
        })
        self.assertEqual(proposal["preview"]["replaces_correction"], "admin")
        self.assertEqual(proposal["preview"]["last_7_tracked_days"]["current_categories"], {"admin": 0.5})

        change = changes.apply(self.conn, self.config, ledger.proposals[0])
        self.assertEqual(self.correction(), "productive_core")
        self.assertEqual(changes.list_changes(self.conn)[0]["id"], change["id"])
        changes.undo(self.conn, self.config, str(change["id"]))
        self.assertEqual(self.correction(), "admin")

    def correction(self) -> str:
        return self.conn.execute(
            "SELECT category FROM manual_corrections WHERE scope = 'process' AND pattern = 'code.exe'"
        ).fetchone()[0]

    def test_analysis_collects_proposals_thinking_and_history_without_writing(self) -> None:
        model = ScriptedModel(
            {**tool_call("mark_absent", {"date": "2026-07-20", "label": "sleep", "app": "chrome.exe"}),
             "thinking": "The user said chrome time was sleep."},
            answer("已提议把 chrome.exe 的 0.2 小时标为睡觉，等你批准。"),
        )
        seen: list[str] = []
        with mock.patch.object(llm_service, "chat", model), \
                mock.patch.object(llm_service, "model_capabilities", return_value=("tools",)):
            result = analyst.analyze(
                self.conn, self.config, "那段是在睡觉，帮我标一下",
                tool_mode="native",
                history=[{"question": "7月20日 chrome 用了多久？", "answer": "0.2 小时。"}],
                on_proposal=lambda p: seen.append(p.summary),
            )

        self.assertEqual(len(result.proposals), 1)
        self.assertEqual(seen, [result.proposals[0].summary])
        self.assertEqual(result.thinking, ["The user said chrome time was sleep."])
        roles = [m["role"] for m in model.calls[0]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(result.ungrounded_numbers, [])
        self.assertEqual(self.annotations(), 0)


class GroundingTests(unittest.TestCase):
    def test_rounded_and_small_numbers_pass_but_new_figures_do_not(self) -> None:
        flagged = analyst.ungrounded_numbers(
            "大约 12 小时，占 35%，第 3 点，比上周多 17.4 小时，2026-07-20 那天最多。",
            ['{"hours": 12.3, "share": 35.2, "date": "2026-07-20"}'],
        )
        self.assertEqual(flagged, ["17.4"])


if __name__ == "__main__":
    unittest.main()
