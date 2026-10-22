"""Tests for Phase 3-9: workday, browser context, classification, deliverables,
distraction alerts, the local-LLM review, and autostart."""

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
from focusledger.services import deliverables as dl
from focusledger.services import llm as llm_service
from focusledger.services import review as review_service
from focusledger.services import workday as wd
from focusledger.services.activity import record_activity_snapshot
from focusledger.services.alerts import (
    DistractionTracker,
    label_distraction,
    list_distractions,
    record_distraction,
)
from focusledger.services.browser import parse_browser_context
from focusledger.services.classify import (
    ADMIN,
    COMMUNICATION,
    ENTERTAINMENT,
    GAMING,
    LEARNING_GENERAL,
    PRODUCTIVE_CORE,
    SCOPE_PROCESS,
    UNKNOWN,
    add_correction,
    attach_attention_weight,
    classify,
    classify_segments,
    load_rule_context,
    unknown_targets,
)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = build_default_config(self.root)
        self.conn = connect(self.config.database_path)
        apply_migrations(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def sample(self, moment: datetime, process: str, title: str, idle: float = 0.0) -> None:
        record_activity_snapshot(
            self.conn,
            device_id="dev",
            snapshot=ActiveWindowSnapshot(
                captured_at=moment, process_id=1, process_name=process,
                executable_path=f"/A/{process}.app", window_title=title,
                window_handle="w1", source="macos_active_window",
            ),
            segment_gap_seconds=15,
            idle_seconds=idle,
        )

    def heartbeat(self, moment: datetime) -> None:
        import uuid

        self.conn.execute(
            "INSERT INTO agent_heartbeats(id, device_id, timestamp, pid, status, message)"
            " VALUES (?, ?, ?, ?, ?, NULL)",
            (uuid.uuid4().hex, "dev", moment.isoformat(), 1, "running"),
        )


# --------------------------------------------------------------------------
class WorkdayTests(Fixture):
    def test_start_pause_resume_end(self) -> None:
        base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        wd.start_workday(self.conn, self.config, at=base)
        wd.pause_workday(self.conn, reason="lunch", at=base + timedelta(hours=3))
        wd.resume_workday(self.conn, at=base + timedelta(hours=4))
        session = wd.end_workday(self.conn, at=base + timedelta(hours=8))

        view = wd.describe(self.conn, self.config, str(session["workday_key"]),
                           now=base + timedelta(hours=8))
        self.assertEqual(view.status, wd.ENDED)
        self.assertAlmostEqual(view.elapsed_seconds, 8 * 3600, delta=2)
        self.assertAlmostEqual(view.paused_seconds, 3600, delta=2)
        self.assertAlmostEqual(view.worked_seconds, 7 * 3600, delta=2)

    def test_second_start_is_refused_while_one_is_open(self) -> None:
        wd.start_workday(self.conn, self.config)
        with self.assertRaises(wd.WorkdayError):
            wd.start_workday(self.conn, self.config)

    def test_work_past_midnight_keeps_the_starting_day_key(self) -> None:
        evening = datetime(2026, 5, 6, 22, 0).astimezone()
        after_midnight = datetime(2026, 5, 7, 2, 30).astimezone()
        self.assertEqual(wd.workday_key_for(evening, self.config), "2026-05-06")
        self.assertEqual(wd.workday_key_for(after_midnight, self.config), "2026-05-06")

    def test_morning_after_start_time_is_its_own_day(self) -> None:
        morning = datetime(2026, 5, 7, 9, 30).astimezone()
        self.assertEqual(wd.workday_key_for(morning, self.config), "2026-05-07")

    def test_missing_data_is_derived_from_heartbeat_gaps(self) -> None:
        base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        for minute in range(0, 10):
            self.heartbeat(base + timedelta(minutes=minute))
        # 30-minute hole here.
        for minute in range(40, 50):
            self.heartbeat(base + timedelta(minutes=minute))
        self.conn.commit()

        intervals = wd.missing_data_intervals(
            self.conn, self.config, base, base + timedelta(minutes=50)
        )
        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(float(intervals[0]["duration_seconds"]), 30 * 60, delta=90)

    def test_archived_activity_counts_as_historical_coverage(self) -> None:
        base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        ended = base + timedelta(minutes=50)
        self.conn.execute(
            """
            INSERT INTO activity_segments(
                id, device_id, started_at, ended_at, duration_seconds,
                process_name, source, created_at, updated_at, local_date,
                started_at_utc, ended_at_utc
            )
            VALUES (
                'archived-coverage', 'dev', ?, ?, 3000,
                'Code', 'retention', ?, ?, ?, ?, ?
            )
            """,
            (
                base.isoformat(),
                ended.isoformat(),
                base.isoformat(),
                ended.isoformat(),
                base.date().isoformat(),
                base.astimezone(timezone.utc).isoformat(),
                ended.astimezone(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

        intervals = wd.missing_data_intervals(self.conn, self.config, base, ended)

        self.assertEqual(intervals, [])

    def test_holiday_never_asks_for_a_review(self) -> None:
        wd.mark_day(self.conn, self.config, "2026-05-06", wd.HOLIDAY)
        session = wd.get_session(self.conn, "2026-05-06")
        self.assertEqual(session["status"], wd.HOLIDAY)
        self.assertEqual(session["review_status"], wd.REVIEW_DONE)
        self.assertEqual(wd.pending_reviews(self.conn, "2026-05-07"), [])

    def test_pending_reviews_lists_ended_days_only(self) -> None:
        base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        wd.start_workday(self.conn, self.config, at=base)
        wd.end_workday(self.conn, at=base + timedelta(hours=4))
        key = wd.workday_key_for(base, self.config)
        tomorrow = "2999-01-01"
        self.assertEqual([r["workday_key"] for r in wd.pending_reviews(self.conn, tomorrow)], [key])
        wd.set_review_status(self.conn, key, wd.REVIEW_DONE)
        self.assertEqual(wd.pending_reviews(self.conn, tomorrow), [])


# --------------------------------------------------------------------------
class BrowserContextTests(unittest.TestCase):
    def test_site_is_recovered_from_page_title(self) -> None:
        ctx = parse_browser_context("Google Chrome", "async in python - Stack Overflow - Google Chrome")
        self.assertTrue(ctx.is_browser)
        self.assertEqual(ctx.site, "stackoverflow.com")
        self.assertEqual(ctx.page_title, "async in python - Stack Overflow")

    def test_non_browser_process_is_not_parsed(self) -> None:
        ctx = parse_browser_context("PyCharm", "main.py - Project")
        self.assertFalse(ctx.is_browser)
        self.assertIsNone(ctx.site)

    def test_redacted_private_title_is_not_mined_for_a_site(self) -> None:
        ctx = parse_browser_context("Google Chrome", "[private browsing]")
        self.assertTrue(ctx.is_private)
        self.assertIsNone(ctx.site)

    def test_bare_domain_in_title_is_picked_up(self) -> None:
        ctx = parse_browser_context("Firefox", "docs.rs/tokio/latest - Mozilla Firefox")
        self.assertEqual(ctx.site, "docs.rs")


# --------------------------------------------------------------------------
class ClassifyTests(Fixture):
    def test_ide_is_core_productive(self) -> None:
        self.assertEqual(classify("PyCharm", "report.py").category, PRODUCTIVE_CORE)

    def test_video_site_is_split_by_page_title(self) -> None:
        lecture = classify("Google Chrome", "VAE derivation lecture - YouTube - Google Chrome")
        gameplay = classify("Google Chrome", "Elden Ring gameplay - YouTube - Google Chrome")
        self.assertEqual(lecture.category, LEARNING_GENERAL)
        self.assertEqual(gameplay.category, ENTERTAINMENT)

    def test_steam_is_gaming(self) -> None:
        self.assertEqual(classify("Steam", "Steam").category, GAMING)

    def test_game_store_install_folder_is_gaming(self) -> None:
        windows_path = (
            "C:\\Program Files (x86)\\Steam\\steamapps\\common\\"
            "Danganronpa V3 Killing Harmony\\Dangan3Win.exe"
        )
        mac_path = (
            "/Users/me/Library/Application Support/Steam/steamapps/common/"
            "Terraria/Terraria.app"
        )
        self.assertEqual(
            classify("Dangan3Win.exe", "Danganronpa V3", executable_path=windows_path).category,
            GAMING,
        )
        self.assertEqual(
            classify("Terraria", "Terraria", executable_path=mac_path).category, GAMING
        )
        self.assertEqual(classify("Dangan3Win.exe", "Danganronpa V3").category, UNKNOWN)
        segments = classify_segments(
            [{"process_name": "Dangan3Win.exe", "window_title": "Danganronpa V3",
              "executable_path": windows_path}],
            load_rule_context(self.conn),
        )
        self.assertEqual(segments[0]["category"], GAMING)

    def test_correction_overrides_the_game_library_rule(self) -> None:
        # Steam also distributes tools, so a correction still has to win.
        add_correction(self.conn, "dev", SCOPE_PROCESS, "blender.exe", PRODUCTIVE_CORE)
        result = classify(
            "blender.exe",
            "Blender",
            load_rule_context(self.conn),
            executable_path="D:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe",
        )
        self.assertEqual(result.category, PRODUCTIVE_CORE)

    def test_common_windows_binaries_are_known(self) -> None:
        self.assertEqual(classify("pycharm64.exe", "report.py").category, PRODUCTIVE_CORE)
        self.assertEqual(classify("Weixin.exe", "\u5fae\u4fe1").category, COMMUNICATION)
        self.assertEqual(classify("olk.exe", "Inbox").category, ADMIN)

    def test_manual_correction_wins_over_built_in_rules(self) -> None:
        add_correction(self.conn, "dev", SCOPE_PROCESS, "steam", PRODUCTIVE_CORE)
        context = load_rule_context(self.conn)
        result = classify("Steam", "Steam", context)
        self.assertEqual(result.category, PRODUCTIVE_CORE)
        self.assertEqual(result.matched_by, "manual")

    def test_deliverable_keyword_links_activity(self) -> None:
        dl.add_deliverable(self.conn, "FocusLedger MVP", keywords="focusledger")
        context = load_rule_context(self.conn)
        result = classify("PyCharm", "report.py - FocusLedger", context)
        self.assertEqual(result.matched_by, "deliverable")
        self.assertIsNotNone(result.deliverable_id)

    def test_unknown_targets_use_the_real_app_name(self) -> None:
        """Regression: timeline rows key the app as `process_name`.

        Reading only `process` yielded "(unknown)" as the pattern, which made
        both the fix-up command and the model prompt useless.
        """
        classified = [{
            "process_name": "Figma", "window_title": "Untitled - design",
            "category": UNKNOWN, "duration_seconds": 600, "site": None,
        }]
        targets = unknown_targets(classified)
        self.assertEqual(targets[0]["pattern"], "figma")
        self.assertEqual(targets[0]["sample_title"], "Untitled - design")

    def test_llm_labels_are_validated_before_being_stored(self) -> None:
        from focusledger.services.classify import (
            add_rule, build_label_prompt, label_unknowns_with_llm, list_rules, parse_label_reply,
        )

        targets = [
            {"scope": "process", "pattern": "figma", "seconds": 600, "sample_title": "design"},
            {"scope": "site", "pattern": "example.io", "seconds": 200, "sample_title": "blog"},
        ]
        prompt = build_label_prompt(targets)
        self.assertIn("figma", prompt)
        self.assertIn("productive_core", prompt)

        messy = (
            "Sure, here you go:\n"
            "figma => productive_core\n"
            "example.io => learning_general\n"
            "hallucinated => gaming\n"      # pattern was never asked about
            "figma => social\n"             # duplicate, must not override
            "example.io => banana\n"        # not a real category
        )
        labels = parse_label_reply(messy, targets)
        self.assertEqual(
            {(l["pattern"], l["category"]) for l in labels},
            {("figma", "productive_core"), ("example.io", "learning_general")},
        )

        with mock.patch.object(llm_service, "generate", return_value=messy):
            stored = label_unknowns_with_llm(self.conn, self.config, targets)
        self.assertEqual(len(stored), 2)
        patterns = {str(r["pattern"]) for r in list_rules(self.conn)}
        self.assertEqual(patterns, {"figma", "example.io"})
        self.assertNotIn("hallucinated", patterns)

    def test_learned_rule_beats_builtin_but_loses_to_manual(self) -> None:
        from focusledger.services.classify import add_rule

        add_rule(self.conn, SCOPE_PROCESS, "steam", PRODUCTIVE_CORE, note="llm:test")
        result = classify("Steam", "Steam", load_rule_context(self.conn))
        self.assertEqual(result.category, PRODUCTIVE_CORE)
        self.assertEqual(result.matched_by, "learned")

        add_correction(self.conn, "dev", SCOPE_PROCESS, "steam", ENTERTAINMENT)
        result = classify("Steam", "Steam", load_rule_context(self.conn))
        self.assertEqual(result.category, ENTERTAINMENT)
        self.assertEqual(result.matched_by, "manual")

    def test_unknown_targets_rank_by_time(self) -> None:
        classified = [
            {"process": "WeirdApp", "title": "x", "category": UNKNOWN, "duration_seconds": 300,
             "site": None},
            {"process": "OtherApp", "title": "y", "category": UNKNOWN, "duration_seconds": 900,
             "site": None},
        ]
        targets = unknown_targets(classified)
        self.assertEqual(targets[0]["pattern"], "otherapp")

    def test_attention_weight_scales_effective_time(self) -> None:
        base = datetime(2026, 5, 6, 9, 0).astimezone()
        classified = [{
            "started_at": base.isoformat(),
            "ended_at": (base + timedelta(minutes=10)).isoformat(),
            "duration_seconds": 600,
        }]
        attention = [{
            "started_at": base.isoformat(),
            "ended_at": (base + timedelta(minutes=10)).isoformat(),
            "attention_weight": 0.5,
        }]
        attach_attention_weight(classified, attention)
        self.assertAlmostEqual(classified[0]["attention_weight"], 0.5, places=3)


# --------------------------------------------------------------------------
class DeliverableTests(Fixture):
    def test_time_is_attributed_by_keyword(self) -> None:
        item = dl.add_deliverable(self.conn, "FocusLedger MVP", keywords="focusledger")
        classified = [
            {"process": "PyCharm", "title": "report.py - FocusLedger",
             "duration_seconds": 600, "attention_weight": 1.0, "deliverable_id": None},
            {"process": "Steam", "title": "Steam",
             "duration_seconds": 600, "attention_weight": 1.0, "deliverable_id": None},
        ]
        attribution = dl.attribute_time(self.conn, classified)
        self.assertEqual(len(attribution), 1)
        self.assertEqual(attribution[0]["id"], item["id"])
        self.assertAlmostEqual(float(attribution[0]["seconds"]), 600, delta=1)

    def test_effective_time_respects_attention_weight(self) -> None:
        dl.add_deliverable(self.conn, "FocusLedger MVP", keywords="focusledger")
        classified = [{
            "process": "PyCharm", "title": "x - FocusLedger",
            "duration_seconds": 600, "attention_weight": 0.5, "deliverable_id": None,
        }]
        attribution = dl.attribute_time(self.conn, classified)
        self.assertAlmostEqual(float(attribution[0]["effective_seconds"]), 300, delta=1)

    def test_untouched_deliverables_are_reported_as_stalled(self) -> None:
        dl.add_deliverable(self.conn, "Touched", keywords="pycharm")
        dl.add_deliverable(self.conn, "Ignored", keywords="nothing-matches-this")
        classified = [{"process": "PyCharm", "title": "x", "duration_seconds": 60,
                       "attention_weight": 1.0, "deliverable_id": None}]
        attribution = dl.attribute_time(self.conn, classified)
        stalled = dl.stalled_deliverables(self.conn, attribution)
        self.assertEqual([s["title"] for s in stalled], ["Ignored"])

    def test_daily_note_is_upserted(self) -> None:
        dl.save_daily_note(self.conn, "2026-05-06", "a", "b", "c")
        dl.save_daily_note(self.conn, "2026-05-06", "a2", "b2", "c2")
        note = dl.get_daily_note(self.conn, "2026-05-06")
        self.assertEqual(note["progress"], "a2")
        rows = self.conn.execute("SELECT COUNT(*) c FROM daily_notes").fetchone()
        self.assertEqual(rows["c"], 1)


# --------------------------------------------------------------------------
class DistractionTests(Fixture):
    def test_alert_fires_only_after_the_threshold(self) -> None:
        tracker = DistractionTracker(600, 900, 900)
        now = datetime.now().astimezone()
        for _ in range(119):  # 119 * 5s = 595s, just under 10 minutes
            self.assertIsNone(tracker.observe(GAMING, 5, now))
        fired = tracker.observe(GAMING, 5, now)
        self.assertIsNotNone(fired)
        self.assertEqual(fired[0], "gaming")

    def test_switching_category_resets_the_streak(self) -> None:
        tracker = DistractionTracker(600, 900, 900)
        now = datetime.now().astimezone()
        for _ in range(100):
            tracker.observe(GAMING, 5, now)
        tracker.observe(PRODUCTIVE_CORE, 5, now)
        for _ in range(50):
            self.assertIsNone(tracker.observe(GAMING, 5, now))

    def test_cooldown_suppresses_repeat_alerts(self) -> None:
        tracker = DistractionTracker(60, 60, 900)
        now = datetime.now().astimezone()
        for _ in range(12):
            tracker.observe(GAMING, 5, now)
        self.assertIsNone(tracker.observe(GAMING, 5, now + timedelta(seconds=60)))
        later = tracker.observe(GAMING, 5, now + timedelta(seconds=1000))
        self.assertIsNotNone(later)

    def test_events_can_be_labelled_with_a_reason(self) -> None:
        event_id = record_distraction(
            self.conn, "dev", "2026-05-06", "gaming", GAMING, "Steam", 900
        )
        self.assertTrue(label_distraction(self.conn, event_id, "procrastination"))
        events = list_distractions(self.conn, "2026-05-06")
        self.assertEqual(events[0]["reason"], "procrastination")

    def test_unknown_reason_is_rejected(self) -> None:
        event_id = record_distraction(self.conn, "dev", "2026-05-06", "gaming", GAMING, "Steam", 900)
        with self.assertRaises(ValueError):
            label_distraction(self.conn, event_id, "not-a-real-reason")


# --------------------------------------------------------------------------
class ReviewTests(Fixture):
    def seed_day(self) -> str:
        base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        for i in range(120):
            self.sample(base + timedelta(seconds=i * 5), "PyCharm", "report.py - FocusLedger")
        self.conn.commit()
        return base.date().isoformat()

    def test_review_falls_back_when_no_model_is_reachable(self) -> None:
        key = self.seed_day()
        dl.add_deliverable(self.conn, "FocusLedger MVP", keywords="focusledger")
        with mock.patch.object(
            llm_service, "generate", side_effect=llm_service.LlmUnavailable("no server")
        ):
            result = review_service.generate_review(self.conn, self.config, key)

        self.assertEqual(result["llm_status"], review_service.LLM_FAILED)
        self.assertEqual(result["generator"], "rules")
        markdown = str(result["markdown"])
        for heading in ["# Daily Review", "## Workday", "## Objective Stats",
                        "## Deliverable Progress", "## Attention and Distractions",
                        "## Review", "## Tomorrow Plan", "## Missing Data",
                        "## Manual Corrections"]:
            self.assertIn(heading, markdown)

    def test_review_uses_the_model_when_one_answers(self) -> None:
        key = self.seed_day()
        canned = "## Review\n\n- did things\n\n## Tomorrow Plan\n\n- 09:00-10:00 do more\n"
        with mock.patch.object(llm_service, "generate", return_value=canned) as generate:
            result = review_service.generate_review(self.conn, self.config, key)
        self.assertEqual(result["llm_status"], review_service.LLM_OK)
        self.assertIn("did things", str(result["markdown"]))
        # The model must only ever be handed finished figures, never raw rows.
        briefing = generate.call_args[0][2]
        self.assertNotIn("started_at", briefing)

    def test_missing_headings_from_a_small_model_are_repaired(self) -> None:
        key = self.seed_day()
        with mock.patch.object(llm_service, "generate", return_value="just some prose"):
            result = review_service.generate_review(self.conn, self.config, key)
        self.assertIn("## Review", str(result["markdown"]))
        self.assertIn("## Tomorrow Plan", str(result["markdown"]))

    def test_review_is_saved_and_marks_the_workday_reviewed(self) -> None:
        key = self.seed_day()
        wd.start_workday(self.conn, self.config)
        with mock.patch.object(
            llm_service, "generate", side_effect=llm_service.LlmUnavailable("no server")
        ):
            review_service.generate_review(self.conn, self.config, key)
        saved = review_service.get_review(self.conn, key)
        self.assertIsNotNone(saved)
        session = wd.get_session(self.conn, key)
        if session:
            self.assertEqual(session["review_status"], wd.REVIEW_DONE)

    def test_journal_file_is_written(self) -> None:
        key = self.seed_day()
        path = review_service.write_journal(self.config, key, "# hello\n")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), "# hello\n")


# --------------------------------------------------------------------------
class LlmTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = build_default_config(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_disabled_config_reports_unavailable_without_a_request(self) -> None:
        config = type(self.config)(**{**self.config.__dict__, "llm_enabled": False})
        status = llm_service.probe(config)
        self.assertFalse(status.available)
        with self.assertRaises(llm_service.LlmUnavailable):
            llm_service.generate(config, "s", "u")

    def test_unreachable_endpoint_degrades_rather_than_crashing(self) -> None:
        config = type(self.config)(
            **{**self.config.__dict__, "llm_base_url": "http://127.0.0.1:9"}
        )
        status = llm_service.probe(config, timeout=1.0)
        self.assertFalse(status.available)
        self.assertIn("cannot reach", status.detail)

    def test_loopback_requests_bypass_a_configured_proxy(self) -> None:
        """Regression: a campus/corporate proxy must not intercept 127.0.0.1.

        urllib honours http_proxy for every request, so without an explicit
        bypass a local model endpoint is handed to the proxy, which answers
        403 and makes a perfectly healthy local model look unreachable.
        """
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {
            "http_proxy": "http://proxy.invalid:3128",
            "HTTP_PROXY": "http://proxy.invalid:3128",
            "all_proxy": "http://proxy.invalid:3128",
        }):
            status = llm_service.probe(self.config, timeout=2.0)
        # It must fail by connecting directly (refused), never via the proxy.
        self.assertFalse(status.available)
        self.assertNotIn("forbidden", status.detail.lower())
        self.assertNotIn("proxy", status.detail.lower())

    def test_loopback_detection(self) -> None:
        for url in ["http://127.0.0.1:11434", "http://localhost:11434", "http://127.5.0.1:1"]:
            self.assertTrue(llm_service.is_loopback(url), url)
        self.assertFalse(llm_service.is_loopback("http://ollama.example.com:11434"))

    def test_model_name_matching_tolerates_the_latest_tag(self) -> None:
        self.assertTrue(llm_service._same_model("qwen2.5:latest", "qwen2.5"))
        self.assertTrue(llm_service._same_model("qwen2.5:1.5b", "qwen2.5:1.5b"))
        self.assertFalse(llm_service._same_model("llama3.2:1b", "qwen2.5:1.5b"))


# --------------------------------------------------------------------------
class AutostartTests(unittest.TestCase):
    def test_status_never_raises_on_any_platform(self) -> None:
        from focusledger.services import autostart

        status = autostart.status()
        self.assertIsInstance(status.platform_name, str)
        self.assertIsInstance(status.installed, bool)

    def test_agent_command_runs_the_module_not_a_shell_string(self) -> None:
        from focusledger.services import autostart

        with tempfile.TemporaryDirectory() as tmp:
            config = build_default_config(Path(tmp))
            command = autostart._agent_command(config)
        self.assertIn("-m", command)
        self.assertIn("focusledger", command)
        self.assertIn("--background-child", command)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
class RegressionTests(Fixture):
    """Bugs found by probing edge cases; each of these once shipped broken."""

    def test_review_covers_a_workday_that_crosses_midnight(self) -> None:
        """The digest used to read only the key's calendar date.

        A 22:00-01:30 session lost every sample after midnight — the exact case
        the workday model exists to handle.
        """
        start = datetime(2026, 5, 6, 22, 0).astimezone()
        for i in range(210):
            self.sample(start + timedelta(minutes=i), "PyCharm", "main.py")
        self.conn.commit()
        wd.start_workday(self.conn, self.config, at=start)
        wd.end_workday(self.conn, at=start + timedelta(minutes=210))

        key = wd.workday_key_for(start, self.config)
        self.assertEqual(key, "2026-05-06")
        digest = review_service.build_digest(self.conn, self.config, key)
        self.assertGreater(digest["attention"]["raw_seconds"], 3.3 * 3600)

    def test_missing_data_survives_a_naive_timestamp(self) -> None:
        """A naive row used to raise TypeError against the aware boundaries."""
        import uuid

        self.conn.execute(
            "INSERT INTO agent_heartbeats(id, device_id, timestamp, pid, status, message)"
            " VALUES (?, ?, ?, ?, ?, NULL)",
            (uuid.uuid4().hex, "dev", "2026-05-06T09:00:00", 1, "running"),
        )
        self.conn.commit()
        intervals = wd.missing_data_intervals(
            self.conn, self.config,
            datetime(2026, 5, 6, 8, 0).astimezone(),
            datetime(2026, 5, 6, 12, 0).astimezone(),
        )
        self.assertIsInstance(intervals, list)

    def test_attention_weighting_survives_mixed_awareness(self) -> None:
        base = datetime(2026, 5, 6, 9, 0)
        classified = [{
            "started_at": base.isoformat(),
            "ended_at": (base + timedelta(minutes=10)).isoformat(),
            "duration_seconds": 600,
        }]
        attention = [{
            "started_at": base.astimezone().isoformat(),
            "ended_at": (base + timedelta(minutes=10)).astimezone().isoformat(),
            "attention_weight": 0.5,
        }]
        attach_attention_weight(classified, attention)
        self.assertAlmostEqual(classified[0]["attention_weight"], 0.5, places=2)

    def test_short_keyword_cannot_swallow_unrelated_activity(self) -> None:
        """`a` used to match "engineering chat" and claim the whole day."""
        from focusledger.services.deliverables import keyword_matches

        self.assertFalse(keyword_matches("a", "engineering chat"))
        self.assertFalse(keyword_matches("app", "happy chatting"))
        self.assertFalse(keyword_matches("api", "rapid prototyping"))
        self.assertTrue(keyword_matches("api", "API design doc"))
        self.assertTrue(keyword_matches("focusledger", "report.py - FocusLedger"))
        self.assertTrue(keyword_matches("报告", "季度报告初稿"))

    def test_one_letter_keyword_does_not_link_a_deliverable(self) -> None:
        dl.add_deliverable(self.conn, "A", keywords="a")
        result = classify("Slack", "engineering chat", load_rule_context(self.conn))
        self.assertNotEqual(result.matched_by, "deliverable")

    def test_productive_share_cannot_exceed_100_percent(self) -> None:
        """The fallback compared raw foreground time to weighted attention.

        A window left open while idle inflated raw productive time above the
        effective total, so the review read "(159%)". The comparison now uses
        the weighted productive figure and is clamped.
        """
        digest = {
            "productive_seconds": 15 * 3600,          # raw foreground, idle included
            "distracting_seconds": 0,
            "productive_effective_seconds": 8 * 3600,  # weighted
            "distracting_effective_seconds": 0,
            "attention": {"effective_seconds": 9.5 * 3600},
            "attribution": [], "stalled": [], "open_tasks": [],
            "distraction_reasons": {}, "note": None,
        }
        prose = review_service._fallback_prose(digest)
        import re
        pct = int(re.search(r"\((\d+)%\)", prose).group(1))
        self.assertLessEqual(pct, 100)

    def test_attention_weight_never_exceeds_one(self) -> None:
        """Overlapping attention spans could sum past 1.0 and inflate effort."""
        base = datetime(2026, 5, 6, 9, 0).astimezone()
        classified = [{
            "started_at": base.isoformat(),
            "ended_at": (base + timedelta(minutes=10)).isoformat(),
            "duration_seconds": 600,
        }]
        span = {
            "started_at": base.isoformat(),
            "ended_at": (base + timedelta(minutes=10)).isoformat(),
            "attention_weight": 1.0,
        }
        attach_attention_weight(classified, [span, dict(span)])
        self.assertLessEqual(classified[0]["attention_weight"], 1.0)
