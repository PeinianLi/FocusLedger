from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from focusledger.agent.runner import _read_idle_seconds
from focusledger.db.connection import connect
from focusledger.db.migrations import apply_migrations
from focusledger.platform.macos import MacOSPlatformAdapter
from focusledger.services.privacy import (
    PRIVATE_BROWSER_TITLE,
    redact_existing_private_browser_titles,
)


def make_subprocess_adapter() -> MacOSPlatformAdapter:
    with patch.object(MacOSPlatformAdapter, "_select_backend", return_value="subprocess"):
        return MacOSPlatformAdapter()


def fake_run(*, front_pid: str, title_pid: str, title: str):
    """Stand in for the lsappinfo/osascript/ioreg commands.

    ``front_pid`` is what lsappinfo reports, ``title_pid`` what osascript
    reports; they differ when the user switches apps mid-sample.
    """

    def run(args: list[str]) -> str:
        if args[:2] == ["lsappinfo", "front"]:
            return "ASN:0x0-0x1e01e:\n"
        if args[:2] == ["lsappinfo", "info"]:
            return {
                "name": '"LSDisplayName"="Google Chrome"',
                "bundlepath": '"LSBundlePath"="/Applications/Google Chrome.app"',
                "pid": f'"pid"={front_pid}',
            }[args[3]]
        if args[0] == "osascript":
            return f"{title_pid}\n{title}\n"
        return ""

    return run


class FrontWindowParsingTests(unittest.TestCase):
    def test_parses_owner_pid_and_title(self) -> None:
        self.assertEqual(
            MacOSPlatformAdapter._parse_front_window("501\nFocusLedger - README.md\n"),
            (501, "FocusLedger - README.md"),
        )

    def test_empty_title_is_none(self) -> None:
        self.assertEqual(MacOSPlatformAdapter._parse_front_window("501\n\n"), (501, None))

    def test_no_output_is_unknown(self) -> None:
        self.assertEqual(MacOSPlatformAdapter._parse_front_window(""), (None, None))

    def test_unexpected_shape_is_rejected(self) -> None:
        self.assertEqual(
            MacOSPlatformAdapter._parse_front_window("execution error: not authorized\n"),
            (None, None),
        )

    def test_title_containing_separator_is_kept_whole(self) -> None:
        owner_pid, title = MacOSPlatformAdapter._parse_front_window("501\na\nb\n")
        self.assertEqual((owner_pid, title), (501, "a\nb"))


class MacOSIdleTests(unittest.TestCase):
    def test_idle_seconds_parsed_from_ioreg(self) -> None:
        adapter = make_subprocess_adapter()
        with patch.object(
            MacOSPlatformAdapter, "_run", staticmethod(lambda args: '  | "HIDIdleTime" = 4500000000\n')
        ):
            self.assertAlmostEqual(adapter.get_idle_seconds(), 4.5)

    def test_missing_hid_idle_time_raises_instead_of_reporting_zero(self) -> None:
        # 0.0 would mean "input just happened" and score the sample as fully
        # attentive; a failed read must not be able to inflate effective time.
        adapter = make_subprocess_adapter()
        with patch.object(MacOSPlatformAdapter, "_run", staticmethod(lambda args: "")):
            with self.assertRaises(RuntimeError):
                adapter.get_idle_seconds()

    def test_agent_records_failed_idle_read_as_unknown(self) -> None:
        adapter = make_subprocess_adapter()
        with patch.object(MacOSPlatformAdapter, "_run", staticmethod(lambda args: "")):
            self.assertIsNone(_read_idle_seconds(adapter))


class MacOSActiveWindowTests(unittest.TestCase):
    def test_title_is_kept_when_it_belongs_to_the_sampled_app(self) -> None:
        adapter = make_subprocess_adapter()
        run = fake_run(front_pid="501", title_pid="501", title="Example - Google Chrome")
        with patch.object(MacOSPlatformAdapter, "_run", staticmethod(run)):
            snapshot = adapter.get_active_window()
        self.assertEqual(snapshot.process_name, "Google Chrome")
        self.assertEqual(snapshot.process_id, 501)
        self.assertEqual(snapshot.executable_path, "/Applications/Google Chrome.app")
        self.assertEqual(snapshot.window_title, "Example - Google Chrome")

    def test_title_is_dropped_when_front_app_changed_mid_sample(self) -> None:
        # lsappinfo saw Chrome, osascript saw a different pid a moment later.
        # Filing Slack's title under Chrome would be worse than no title.
        adapter = make_subprocess_adapter()
        run = fake_run(front_pid="501", title_pid="999", title="Slack - general")
        with patch.object(MacOSPlatformAdapter, "_run", staticmethod(run)):
            snapshot = adapter.get_active_window()
        self.assertEqual(snapshot.process_name, "Google Chrome")
        self.assertIsNone(snapshot.window_title)


class HandlelessPrivacyRedactionTests(unittest.TestCase):
    """The macOS subprocess backend stores window_handle as NULL."""

    def open_db(self, tmp: str):
        conn = connect(Path(tmp) / "focusledger.sqlite3")
        apply_migrations(conn)
        return conn

    def insert_event(self, conn, event_id: str, title: str, handle: str | None) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO applications(process_name, executable_path, first_seen_at)
            VALUES ('Google Chrome', '/Applications/Google Chrome.app', '2026-07-17T09:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO raw_activity_events(
                device_id, captured_at_utc, local_date, process_id,
                application_id, window_title, window_handle, source
            )
            VALUES ('device_test', ?, '2026-07-17', 501,
                    (SELECT id FROM applications WHERE process_name = 'Google Chrome'),
                    ?, ?, 'macos_active_window')
            """,
            (
                f"2026-07-17T09:00:0{event_id[-1]}+00:00",
                title,
                handle,
            ),
        )
        conn.commit()

    def titles(self, conn) -> list[str]:
        return [
            row["window_title"]
            for row in conn.execute(
                "SELECT window_title FROM raw_activity_events ORDER BY captured_at_utc"
            ).fetchall()
        ]

    def test_marked_title_is_redacted_without_a_window_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.open_db(tmp)
            self.insert_event(conn, "raw1", "New Incognito Tab - Google Chrome", None)

            counts = redact_existing_private_browser_titles(conn)

            self.assertEqual(counts["raw_activity_events"], 1)
            self.assertEqual(self.titles(conn), [PRIVATE_BROWSER_TITLE])
            conn.close()

    def test_already_redacted_rows_are_not_counted_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.open_db(tmp)
            self.insert_event(conn, "raw1", PRIVATE_BROWSER_TITLE, None)

            counts = redact_existing_private_browser_titles(conn)

            self.assertEqual(counts["raw_activity_events"], 0)
            conn.close()

    def test_normal_browser_titles_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.open_db(tmp)
            self.insert_event(conn, "raw1", "Example Domain - Google Chrome", None)

            redact_existing_private_browser_titles(conn)

            self.assertEqual(self.titles(conn), ["Example Domain - Google Chrome"])
            conn.close()

    def test_handle_data_still_uses_the_sticky_window_rule(self) -> None:
        # Windows and the macOS pyobjc backend do report a handle, so a window
        # once seen as private stays redacted after its title changes.
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.open_db(tmp)
            self.insert_event(conn, "raw1", "New Incognito Tab - Google Chrome", "h1")
            self.insert_event(conn, "raw2", "My bank account - Google Chrome", "h1")

            counts = redact_existing_private_browser_titles(conn)

            self.assertEqual(counts["raw_activity_events"], 2)
            self.assertEqual(
                self.titles(conn), [PRIVATE_BROWSER_TITLE, PRIVATE_BROWSER_TITLE]
            )
            conn.close()


if __name__ == "__main__":
    unittest.main()
