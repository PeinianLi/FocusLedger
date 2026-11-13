from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from focusledger.cli import build_parser
from focusledger.commands import mcp_cmd
from focusledger.config import build_default_config
from focusledger.db.connection import connect
from focusledger.db.migrations import SCHEMA_VERSION, apply_migrations
from focusledger.models import ActiveWindowSnapshot
from focusledger.services import analyst, mcp
from focusledger.services.activity import record_activity_snapshot
from focusledger.services.classify import well_known_sites

REPO_ROOT = Path(__file__).resolve().parents[1]
MODERN = "2026-07-28"
META = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientCapabilities": {},
}
SERVER_INFO = "io.modelcontextprotocol/serverInfo"
DAY = "2026-07-20"

# Words that exist only inside window titles in the fixture. None of them may
# reach a client unless titles were asked for.
TITLE_ONLY_WORDS = ("secret-project", "聊天记录", "QUOKKA", "diagnosis", "narwhalcorp", "jane.doe", "cats")
# One valid set of arguments per served tool.
TOOL_ARGUMENTS = {
    "list_days": {},
    "day_summary": {"date": DAY},
    "range_summary": {"start": DAY, "end": DAY},
    "compare_ranges": {"a_start": DAY, "a_end": DAY, "b_start": DAY, "b_end": DAY},
    "app_usage": {"query": "e"},
    "timeline": {"date": DAY, "min_minutes": "1"},
    "hourly_profile": {},
    "deliverables": {},
}


def request(method: str, request_id: object = 1, meta: dict | None = META, **params: object) -> dict:
    """A JSON-RPC request; ``meta=None`` leaves the per-request metadata out."""
    body = dict(params)
    if meta is not None:
        body["_meta"] = meta
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}


def record(conn, start: datetime, samples: int, process: str, title: str) -> None:
    for index in range(samples):
        record_activity_snapshot(
            conn,
            device_id="dev",
            snapshot=ActiveWindowSnapshot(
                captured_at=start + timedelta(seconds=10 * index),
                process_id=1,
                process_name=process,
                executable_path=f"C:\\Apps\\{process}",
                window_title=title,
                window_handle="1",
            ),
            segment_gap_seconds=15,
            idle_seconds=0.0,
        )


def seed(conn) -> None:
    """One day: an editor, a chat app, and browser windows with telling titles."""
    morning = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
    record(conn, morning, 180, "Code.exe", "main.py - secret-project")
    record(conn, morning + timedelta(minutes=30), 60, "微信.exe", "聊天记录")
    record(conn, morning + timedelta(minutes=40), 60, "chrome.exe",
           "Funny cats - YouTube - Google Chrome")
    record(conn, morning + timedelta(minutes=50), 60, "chrome.exe",
           "QUOKKA diagnosis.report.final.pdf - Google Chrome")
    record(conn, morning + timedelta(minutes=60), 60, "chrome.exe",
           "Inbox - jane.doe@narwhalcorp-internal.com - Google Chrome")


class McpFixture(unittest.TestCase):
    """One tracked day in a throwaway database, served by a fresh server."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = build_default_config(Path(self._tmp.name))
        self.conn = connect(self.config.database_path)
        apply_migrations(self.conn)
        seed(self.conn)
        self.server = mcp.McpServer(self.config)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def ok(self, message: dict, server: mcp.McpServer | None = None) -> dict:
        reply = (server or self.server).handle_message(message)
        self.assertIsNotNone(reply)
        self.assertNotIn("error", reply, reply)
        self.assertEqual(reply["id"], message["id"])
        return reply["result"]

    def fails(self, message: object, code: int) -> dict:
        reply = self.server.handle_message(message)
        self.assertIsNotNone(reply)
        self.assertNotIn("result", reply, reply)
        self.assertEqual(reply["error"]["code"], code)
        return reply

    def call(self, name: str, server: mcp.McpServer | None = None, **arguments: object) -> dict:
        return self.ok(request("tools/call", name=name, arguments=arguments), server)

    def initialize(self, version: object) -> dict:
        """The handshake that revisions up to 2025-11-25 open with."""
        return self.ok(request(
            "initialize", 0, meta=None, protocolVersion=version, capabilities={},
            clientInfo={"name": "test-client", "version": "0"},
        ))


class PerRequestMetadataTests(McpFixture):
    """The 2026-07-28 revision: no handshake, every request describes itself."""

    def test_discover_reports_versions_capabilities_and_identity(self) -> None:
        result = self.ok(request("server/discover", "discover-1"))
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"][0], MODERN)
        self.assertIn("2025-11-25", result["supportedVersions"])
        self.assertIn("tools", result["capabilities"])
        self.assertIsInstance(result["ttlMs"], int)
        self.assertIn(result["cacheScope"], ("public", "private"))
        self.assertEqual(result["_meta"][SERVER_INFO]["name"], "focusledger")
        self.assertIn("list_days", result["instructions"])

    def test_request_without_metadata_is_rejected(self) -> None:
        reply = self.fails(request("tools/list", meta=None), mcp.INVALID_PARAMS)
        self.assertIn("protocolVersion", reply["error"]["message"])

    def test_request_without_client_capabilities_is_rejected(self) -> None:
        meta = {"io.modelcontextprotocol/protocolVersion": MODERN}
        reply = self.fails(request("tools/list", meta=meta), mcp.INVALID_PARAMS)
        self.assertIn("clientCapabilities", reply["error"]["message"])

    def test_unsupported_version_names_the_same_versions_as_discover(self) -> None:
        meta = {**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
        reply = self.fails(request("tools/list", meta=meta), mcp.UNSUPPORTED_PROTOCOL_VERSION)
        advertised = self.ok(request("server/discover"))["supportedVersions"]
        self.assertEqual(reply["error"]["data"], {"supported": advertised, "requested": "1900-01-01"})
        self.assertEqual(reply["error"]["message"], "Unsupported protocol version")

    def test_a_version_that_is_not_a_string_is_malformed(self) -> None:
        for bad in (20260728, ["2026-07-28"], {"v": 1}, True):
            with self.subTest(version=bad):
                meta = {**META, "io.modelcontextprotocol/protocolVersion": bad}
                self.fails(request("tools/list", meta=meta), mcp.INVALID_PARAMS)

    def test_tools_list_serves_exactly_the_read_tools(self) -> None:
        result = self.ok(request("tools/list"))
        names = [tool["name"] for tool in result["tools"]]
        expected = [t.name for t in analyst.TOOLS if t.name not in analyst.WRITE_TOOLS]
        self.assertEqual(names, expected)
        self.assertEqual(len(names), 8)
        self.assertEqual(set(names), set(TOOL_ARGUMENTS))
        for tool in result["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertTrue(tool["description"])
        self.assertEqual(result["resultType"], "complete")
        self.assertIn("ttlMs", result)
        self.assertEqual(result["cacheScope"], "public")

    def test_tool_call_returns_the_same_figures_as_text_and_structure(self) -> None:
        result = self.call("list_days")
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["first_date"], DAY)
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(result["_meta"][SERVER_INFO]["name"], "focusledger")

    def test_arguments_may_be_omitted(self) -> None:
        result = self.ok(request("tools/call", name="list_days"))
        self.assertEqual(result["structuredContent"]["last_date"], DAY)

    def test_bad_arguments_come_back_as_a_tool_error_the_model_can_read(self) -> None:
        result = self.call("day_summary", date="2020-01-01")
        self.assertTrue(result["isError"])
        self.assertNotIn("structuredContent", result)
        self.assertIn("2020-01-01", result["content"][0]["text"])

    def test_unknown_tool_is_a_protocol_error(self) -> None:
        reply = self.fails(request("tools/call", name="drop_everything"), mcp.INVALID_PARAMS)
        self.assertIn("drop_everything", reply["error"]["message"])

    def test_malformed_arguments_are_rejected(self) -> None:
        self.fails(request("tools/call", name="list_days", arguments=DAY), mcp.INVALID_PARAMS)

    def test_unknown_method(self) -> None:
        self.fails(request("resources/list"), mcp.METHOD_NOT_FOUND)

    def test_each_call_reads_the_database_afresh(self) -> None:
        self.assertEqual(self.call("list_days")["structuredContent"]["last_date"], DAY)
        record(self.conn, datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc), 60, "Code.exe", "later.py")
        self.assertEqual(self.call("list_days")["structuredContent"]["last_date"], "2026-07-23")


class ArgumentEdgeTests(McpFixture):
    """Odd but well-formed arguments must end in a usable answer, never a crash."""

    def test_a_threshold_that_is_not_a_finite_number_falls_back_to_the_default(self) -> None:
        def no_constants(token: str):
            raise AssertionError(f"{token} is not JSON")

        for value in ("nan", "inf", "-inf", "1e999", float("inf")):
            with self.subTest(min_minutes=value):
                result = self.call("timeline", date=DAY, min_minutes=value)
                self.assertFalse(result["isError"])
                self.assertEqual(result["structuredContent"]["min_minutes"], 10.0)
                json.loads(result["content"][0]["text"], parse_constant=no_constants)
                json.loads(mcp._encode(result), parse_constant=no_constants)

    def test_a_range_reaching_the_last_representable_date_is_answered(self) -> None:
        for name, arguments in (
            ("range_summary", {"start": DAY, "end": "9999-12-31"}),
            ("range_summary", {"start": "0001-01-01", "end": "9999-12-31"}),
            ("compare_ranges", {"a_start": "0001-01-01", "a_end": DAY,
                                "b_start": DAY, "b_end": "9999-12-31"}),
        ):
            with self.subTest(tool=name, arguments=arguments):
                result = self.call(name, **arguments)
                self.assertFalse(result["isError"], result["content"])

    def test_untracked_gaps_match_a_day_by_day_count(self) -> None:
        def day_by_day(dates: list[str], start: str, end: str) -> list[str]:
            have, gaps, run = set(dates), [], []
            day, last = date.fromisoformat(start), date.fromisoformat(end)
            while day <= last:
                if day.isoformat() in have:
                    if run:
                        gaps.append(f"{run[0]}..{run[-1]}")
                        run = []
                else:
                    run.append(day.isoformat())
                day += timedelta(days=1)
            if run:
                gaps.append(f"{run[0]}..{run[-1]}")
            return gaps

        tracked = ["2026-07-03", "2026-07-04", "2026-07-08", "2026-07-20"]
        for start, end in (
            ("2026-07-03", "2026-07-20"), ("2026-07-01", "2026-07-25"), ("2026-07-04", "2026-07-08"),
            ("2026-07-05", "2026-07-07"), ("2026-07-20", "2026-07-20"), ("2026-07-21", "2026-07-30"),
            ("2026-06-01", "2026-06-05"), ("2026-07-08", "2026-07-08"),
        ):
            with self.subTest(start=start, end=end):
                self.assertEqual(analyst._gaps(tracked, start, end), day_by_day(tracked, start, end))
        self.assertEqual(analyst._gaps(tracked), day_by_day(tracked, tracked[0], tracked[-1]))
        self.assertEqual(analyst._gaps([], "2026-07-01", "2026-07-02"), [])


class ReadOnlyTests(McpFixture):
    def test_proposal_tools_do_not_exist_over_mcp(self) -> None:
        arguments = {
            "mark_absent": {"date": DAY, "label": "sleep", "app": "Code.exe"},
            "correct_category": {"scope": "process", "pattern": "code.exe", "category": "admin"},
        }
        self.assertEqual(set(arguments), set(analyst.WRITE_TOOLS))
        served = {tool["name"] for tool in self.ok(request("tools/list"))["tools"]}
        for name, valid in arguments.items():
            with self.subTest(tool=name):
                # The same arguments do produce a proposal for FocusLedger's own analyst.
                own = analyst.run_tool(analyst.Ledger(self.conn, self.config), name, valid)
                self.assertEqual(own["status"], "awaiting_user_approval")
                self.assertNotIn(name, served)
                reply = self.fails(request("tools/call", name=name, arguments=valid), mcp.INVALID_PARAMS)
                self.assertIn("Unknown tool", reply["error"]["message"])

    def test_the_connection_refuses_writes(self) -> None:
        conn = mcp.open_read_only(self.config.database_path)
        try:
            for statement in (
                "DELETE FROM raw_activity_events",
                "CREATE TABLE intruder(x)",
                "PRAGMA user_version = 99",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.OperationalError):
                    conn.execute(statement)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM raw_activity_events").fetchone()[0], 0)
        finally:
            conn.close()

    def test_every_call_opens_its_own_read_only_connection(self) -> None:
        opened: list[sqlite3.Connection] = []
        real = mcp.open_read_only

        def spy(path):
            conn = real(path)
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM raw_activity_events")
            opened.append(conn)
            return conn

        # The server is built before the patch, so this also proves that the
        # default path goes through open_read_only at call time.
        with mock.patch.object(mcp, "open_read_only", side_effect=spy):
            for name, arguments in TOOL_ARGUMENTS.items():
                with self.subTest(tool=name):
                    self.assertFalse(self.call(name, **arguments)["isError"])
        self.assertEqual(len(opened), len(TOOL_ARGUMENTS))
        self.assertEqual(len({id(conn) for conn in opened}), len(opened))
        for conn in opened:
            with self.assertRaises(sqlite3.ProgrammingError):  # closed after the call
                conn.execute("SELECT 1")

    def test_a_missing_database_is_reported_and_not_created(self) -> None:
        nowhere = Path(self._tmp.name) / "not" / "there"
        server = mcp.McpServer(build_default_config(nowhere))
        result = self.call("list_days", server)
        self.assertTrue(result["isError"])
        self.assertIn("focusledger init", result["content"][0]["text"])
        self.assertNotIn(str(nowhere), json.dumps(result))
        self.assertFalse(nowhere.exists())
        self.assertFalse(nowhere.parent.exists())

    def test_an_outdated_database_is_reported_and_not_upgraded(self) -> None:
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        self.conn.commit()
        result = self.call("list_days")
        self.assertTrue(result["isError"])
        self.assertIn("upgrade", result["content"][0]["text"])
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION - 1)

        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        self.conn.commit()
        newer = self.call("list_days")
        self.assertTrue(newer["isError"])
        self.assertIn("newer", newer["content"][0]["text"])

    def test_a_rollback_journal_database_is_left_in_that_mode(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plain.sqlite3"
            plain = sqlite3.connect(path)
            plain.execute("PRAGMA journal_mode = DELETE")
            plain.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            plain.execute("CREATE TABLE t(x)")
            plain.commit()
            plain.close()
            before = path.read_bytes()
            with contextlib.closing(mcp.open_read_only(path)) as conn:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(path.read_bytes(), before)

    def test_the_read_only_uri_keeps_the_whole_path_out_of_the_host_part(self) -> None:
        if os.name == "nt":
            cases = {
                r"C:\dir a\x&y#%.db": "file:///C:/dir%20a/x%26y%23%25.db?mode=ro",
                r"\\server\share\l.db": "file:////server/share/l.db?mode=ro",
                # The extended-length prefix is kept: it is what makes such a path open.
                r"\\?\C:\d\l.db": "file:////%3F/C:/d/l.db?mode=ro",
                r"\\?\UNC\server\share\l.db": "file:////%3F/UNC/server/share/l.db?mode=ro",
            }
        else:
            cases = {
                "/tmp/dir a/x&y#%.db": "file:///tmp/dir%20a/x%26y%23%25.db?mode=ro",
                # Outside Windows a backslash is part of the name, not a separator.
                "/tmp/back\\slash/l.db": "file:///tmp/back%5Cslash/l.db?mode=ro",
            }
        for path, uri in cases.items():
            with self.subTest(path=path):
                self.assertEqual(mcp.read_only_uri(path), uri)

    @unittest.skipUnless(os.name == "nt", "Windows path forms")
    def test_windows_extended_and_share_paths_can_be_opened(self) -> None:
        plain = os.path.abspath(self.config.database_path)
        forms = {"extended": "\\\\?\\" + plain}
        share = "\\\\localhost\\" + plain[0] + "$" + plain[2:]
        if os.path.isfile(share):  # the administrative share is not always reachable
            forms["share"] = share
        for label, path in forms.items():
            with self.subTest(form=label), contextlib.closing(mcp.open_read_only(path)) as conn:
                self.assertGreater(conn.execute("SELECT COUNT(*) FROM raw_activity_events").fetchone()[0], 0)

    @unittest.skipUnless(os.name == "nt", "Windows path forms")
    def test_a_path_longer_than_the_classic_windows_limit_can_be_opened(self) -> None:
        """Such a path only works with the extended-length prefix, so the prefix must survive."""
        root = "\\\\?\\" + os.path.abspath(self._tmp.name)
        folder = os.path.join(root, *(["L" * 60] * 5))
        database = os.path.join(folder, "focusledger.sqlite3")
        self.assertGreater(len(database), 300)
        os.makedirs(folder)
        try:
            long_db = sqlite3.connect(database)
            long_db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            long_db.execute("CREATE TABLE marker(x)")
            long_db.execute("INSERT INTO marker VALUES ('found')")
            long_db.commit()
            long_db.close()
            with contextlib.closing(mcp.open_read_only(database)) as conn:
                self.assertEqual(conn.execute("SELECT x FROM marker").fetchone()[0], "found")
        finally:
            shutil.rmtree(os.path.join(root, "L" * 60))

    def test_serving_never_changes_the_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config = build_default_config(Path(folder))
            conn = connect(config.database_path)
            apply_migrations(conn)
            seed(conn)
            conn.close()  # a clean close: no companion files left behind
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["focusledger.sqlite3"])
            before = hashlib.sha256(config.database_path.read_bytes()).hexdigest()

            server = mcp.McpServer(config)
            for name, arguments in TOOL_ARGUMENTS.items():
                self.assertFalse(self.call(name, server, **arguments)["isError"])

            self.assertEqual(hashlib.sha256(config.database_path.read_bytes()).hexdigest(), before)
            # SQLite's own companions for a WAL database are the only things that may appear.
            allowed = {"focusledger.sqlite3", "focusledger.sqlite3-wal", "focusledger.sqlite3-shm"}
            self.assertLessEqual({p.name for p in Path(folder).iterdir()}, allowed)

    def test_closing_the_reader_does_not_write_the_log_back_into_the_database(self) -> None:
        """What read-only mode adds over query_only, which only refuses statements.

        When the last connection to a WAL database closes, SQLite copies the
        log into the database file. A read-only connection must not do that.
        """
        with tempfile.TemporaryDirectory() as folder:
            config = build_default_config(Path(folder))
            writer = connect(config.database_path)
            apply_migrations(writer)
            seed(writer)
            reader = mcp.open_read_only(config.database_path)
            try:
                reader.execute("SELECT COUNT(*) FROM raw_activity_events").fetchone()
                record(writer, datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc), 30, "Code.exe", "later.py")
                log = Path(f"{config.database_path}-wal")
                self.assertGreater(log.stat().st_size, 0)  # changes not yet in the database file
                writer.close()  # not the last connection, so nothing is copied back yet
                before = hashlib.sha256(config.database_path.read_bytes()).hexdigest()
            finally:
                reader.close()
            self.assertEqual(hashlib.sha256(config.database_path.read_bytes()).hexdigest(), before)
            self.assertGreater(log.stat().st_size, 0)

    def test_a_data_directory_with_awkward_characters_can_be_opened(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config = build_default_config(Path(folder) / "led ger & #1 %20 数据")
            conn = connect(config.database_path)
            apply_migrations(conn)
            seed(conn)
            conn.close()
            result = self.call("list_days", mcp.McpServer(config))
            self.assertEqual(result["structuredContent"]["first_date"], DAY)


class WindowTitleTests(McpFixture):
    def test_nothing_read_from_a_title_reaches_any_tool_result(self) -> None:
        for name, arguments in TOOL_ARGUMENTS.items():
            with self.subTest(tool=name):
                result = self.call(name, **arguments)
                self.assertFalse(result["isError"], result["content"])
                sent = json.dumps(result, ensure_ascii=False)
                for word in TITLE_ONLY_WORDS:
                    self.assertNotIn(word, sent)

    def test_timeline_has_no_title_field_by_default(self) -> None:
        blocks = self.call("timeline", date=DAY, min_minutes="1")["structuredContent"]["blocks"]
        self.assertGreaterEqual(len(blocks), 3)
        for block in blocks:
            self.assertNotIn("main_title", block)
            self.assertIn("app", block)
        described = {t["name"]: t["description"] for t in self.ok(request("tools/list"))["tools"]}
        self.assertIn("withholds window titles", described["timeline"])
        self.assertNotIn("withholds", described["list_days"])

    def test_only_well_known_sites_are_named(self) -> None:
        sites = {row["site"] for row in self.call("day_summary", date=DAY)["structuredContent"]["top_sites"]}
        self.assertEqual(sites, {"youtube.com", analyst.OTHER_SITE})
        self.assertTrue(sites - {analyst.OTHER_SITE} <= well_known_sites())

    def test_a_client_cannot_probe_titles_through_app_usage(self) -> None:
        for query in ("diagnosis", "narwhalcorp", ".pdf", "report"):
            with self.subTest(query=query):
                self.assertEqual(self.call("app_usage", query=query)["structuredContent"]["matches"], [])
        names = {row["name"] for row in self.call("app_usage", query=".")["structuredContent"]["matches"]}
        self.assertEqual(names, {"Code.exe", "微信.exe", "chrome.exe", "youtube.com"})

    def test_titles_and_every_site_are_returned_when_asked_for(self) -> None:
        server = mcp.McpServer(self.config, include_titles=True)
        blocks = self.call("timeline", server, date=DAY, min_minutes="1")["structuredContent"]["blocks"]
        self.assertIn("main.py - secret-project", [block["main_title"] for block in blocks])
        sites = {row["site"] for row in self.call("day_summary", server, date=DAY)["structuredContent"]["top_sites"]}
        self.assertEqual(sites, {"youtube.com", "diagnosis.report.final.pdf", "narwhalcorp-internal.com"})
        described = {t["name"]: t["description"] for t in self.ok(request("tools/list"), server)["tools"]}
        self.assertNotIn("withholds", described["timeline"])

    def test_the_local_analyst_still_sees_every_site(self) -> None:
        own = analyst.run_tool(analyst.Ledger(self.conn, self.config), "day_summary", {"date": DAY})
        self.assertIn("narwhalcorp-internal.com", {row["site"] for row in own["top_sites"]})
        found = analyst.run_tool(analyst.Ledger(self.conn, self.config), "app_usage", {"query": "narwhalcorp"})
        self.assertEqual([row["name"] for row in found["matches"]], ["narwhalcorp-internal.com"])
        absent = analyst.run_tool(analyst.Ledger(self.conn, self.config), "app_usage", {"query": "zzzz"})
        self.assertTrue(absent["note"].startswith("No app or site name contains this text"))

    def test_the_client_is_told_that_sites_are_grouped(self) -> None:
        described = {t["name"]: t["description"] for t in self.ok(request("tools/list"))["tools"]}
        for name in ("day_summary", "app_usage"):
            self.assertIn(analyst.OTHER_SITE, described[name])
        self.assertNotIn(analyst.OTHER_SITE, described["hourly_profile"])
        self.assertIn(analyst.OTHER_SITE, self.ok(request("server/discover"))["instructions"])
        self.assertIn(analyst.OTHER_SITE, self.initialize("2025-11-25")["instructions"])

        server = mcp.McpServer(self.config, include_titles=True)
        told = json.dumps([self.ok(request("tools/list"), server), self.ok(request("server/discover"), server)])
        self.assertNotIn(analyst.OTHER_SITE, told)
        self.assertNotIn("withh", told)

    def test_an_empty_search_does_not_claim_a_hidden_site_was_unused(self) -> None:
        hidden = self.call("app_usage", query="narwhalcorp")["structuredContent"]
        absent = self.call("app_usage", query="zzzz-never-used")["structuredContent"]
        self.assertEqual(hidden["matches"], [])
        self.assertIn("does not show that no time was spent", hidden["note"])
        # Hidden and absent must be indistinguishable, or the note would be an oracle.
        self.assertEqual({**hidden, "query": ""}, {**absent, "query": ""})
        for limits in ({"start": DAY}, {"end": DAY}, {"start": DAY, "end": DAY},
                       {"start": "2026-08-01", "end": "2026-08-31"}):
            with self.subTest(range=limits):
                one = self.call("app_usage", query="narwhalcorp", **limits)["structuredContent"]
                other = self.call("app_usage", query="zzzz-never-used", **limits)["structuredContent"]
                self.assertEqual({**one, "query": ""}, {**other, "query": ""})
        with_titles = mcp.McpServer(self.config, include_titles=True)
        unhidden = self.call("app_usage", with_titles, query="zzzz-never-used")["structuredContent"]
        self.assertTrue(unhidden["note"].startswith("No app or site name contains this text"))


class HandshakeTests(McpFixture):
    """Revisions up to 2025-11-25: initialize once, then plain requests."""

    def test_initialize_echoes_a_supported_version(self) -> None:
        for version in mcp.LEGACY_VERSIONS:
            with self.subTest(version=version):
                result = self.initialize(version)
                self.assertEqual(result["protocolVersion"], version)
                self.assertIn("tools", result["capabilities"])
                self.assertEqual(result["serverInfo"]["name"], "focusledger")
                self.assertNotIn("resultType", result)

    def test_initialize_offers_the_newest_handshake_version_for_an_unknown_one(self) -> None:
        for unknown in ("1999-01-01", MODERN, None, 20250618):
            with self.subTest(requested=unknown):
                self.assertEqual(self.initialize(unknown)["protocolVersion"], mcp.LEGACY_VERSIONS[0])

    def test_after_the_handshake_requests_need_no_metadata(self) -> None:
        self.initialize("2025-11-25")
        self.assertIsNone(self.server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ))
        listing = self.ok(request("tools/list", meta=None))
        self.assertEqual(len(listing["tools"]), 8)
        self.assertNotIn("resultType", listing)
        self.assertNotIn("ttlMs", listing)
        called = self.ok(request("tools/call", 2, meta=None, name="list_days", arguments={}))
        self.assertEqual(called["structuredContent"]["first_date"], DAY)
        self.assertNotIn("_meta", called)

    def test_ping(self) -> None:
        self.assertEqual(self.ok(request("ping", meta=None)), {})

    def test_a_handshake_client_may_still_send_per_request_metadata(self) -> None:
        self.initialize("2025-11-25")
        self.assertEqual(self.ok(request("tools/list"))["resultType"], "complete")


class BatchTests(McpFixture):
    """Only 2025-03-26 lets a client send several messages as one JSON array."""

    BATCH = [
        request("tools/list", "a", meta=None),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request("ping", "b", meta=None),
        request("no/such/method", "c", meta=None),
    ]

    def test_a_batch_is_answered_in_order_without_replies_to_notifications(self) -> None:
        self.initialize(mcp.BATCH_VERSION)
        replies = self.server.handle_message(self.BATCH)
        self.assertIsInstance(replies, list)
        self.assertEqual([reply["id"] for reply in replies], ["a", "b", "c"])
        self.assertEqual(len(replies[0]["result"]["tools"]), 8)
        self.assertEqual(replies[1]["result"], {})
        self.assertEqual(replies[2]["error"]["code"], mcp.METHOD_NOT_FOUND)

    def test_a_batch_of_notifications_gets_no_reply(self) -> None:
        self.initialize(mcp.BATCH_VERSION)
        self.assertIsNone(self.server.handle_message(
            [{"jsonrpc": "2.0", "method": "notifications/initialized"}] * 2
        ))

    def test_an_empty_batch_is_invalid(self) -> None:
        self.initialize(mcp.BATCH_VERSION)
        self.fails([], mcp.INVALID_REQUEST)

    def test_initialize_inside_a_batch_is_refused(self) -> None:
        self.initialize(mcp.BATCH_VERSION)
        (reply,) = self.server.handle_message([
            request("initialize", "again", meta=None, protocolVersion="2025-11-25", capabilities={}),
        ])
        self.assertEqual(reply["error"]["code"], mcp.INVALID_REQUEST)
        # ... so the session is still the one that allows batches.
        self.assertIsInstance(self.server.handle_message(self.BATCH), list)

    def test_other_revisions_do_not_accept_batches(self) -> None:
        self.fails(self.BATCH, mcp.INVALID_REQUEST)  # no handshake at all
        for version in (v for v in mcp.LEGACY_VERSIONS if v != mcp.BATCH_VERSION):
            with self.subTest(version=version):
                self.initialize(version)
                self.fails(self.BATCH, mcp.INVALID_REQUEST)
        self.fails([request("tools/list")], mcp.INVALID_REQUEST)  # per-request metadata


class MessageTests(McpFixture):
    def test_notifications_get_no_reply(self) -> None:
        for method in ("notifications/initialized", "notifications/cancelled"):
            self.assertIsNone(self.server.handle_message(
                {"jsonrpc": "2.0", "method": method, "params": {"requestId": 1}}
            ))

    def test_invalid_requests(self) -> None:
        self.fails({"jsonrpc": "2.0", "id": 1}, mcp.INVALID_REQUEST)
        self.fails("tools/list", mcp.INVALID_REQUEST)
        for bad_id in (None, 1.5, True, {"a": 1}):
            with self.subTest(id=bad_id):
                reply = self.fails(request("tools/list", bad_id), mcp.INVALID_REQUEST)
                self.assertIsNone(reply["id"])
        self.fails({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1]}, mcp.INVALID_PARAMS)

    def test_a_failing_tool_is_an_internal_error_without_details(self) -> None:
        def broken():
            raise RuntimeError("C:\\Users\\someone\\secret\\path.sqlite3 is locked")

        server = mcp.McpServer(self.config, open_connection=broken)
        with self.assertLogs("focusledger.services.mcp", level="ERROR"):
            reply = server.handle_message(request("tools/call", name="list_days"))
        self.assertEqual(reply["error"]["code"], mcp.INTERNAL_ERROR)
        self.assertNotIn("secret", json.dumps(reply))


class TransportTests(McpFixture):
    def serve(self, *lines: bytes) -> list[bytes]:
        out = io.BytesIO()
        self.server.serve(io.BytesIO(b"".join(lines)), out)
        raw = out.getvalue()
        self.assertTrue(raw.isascii())
        self.assertNotIn(b"\r", raw)
        return raw.split(b"\n")[:-1] if raw else []

    @staticmethod
    def line(message: object) -> bytes:
        return json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"

    def test_one_json_line_per_request_and_nothing_else(self) -> None:
        replies = self.serve(
            self.line(request("server/discover", "d")),
            self.line({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            b"\n",
            b"this is not json\n",
            b"\xff\xfe not utf-8\n",
            self.line(request("tools/call", 7, name="list_days")),
        )
        self.assertEqual(len(replies), 4)
        discover, garbage, not_utf8, call = (json.loads(reply) for reply in replies)
        self.assertEqual(discover["id"], "d")
        for bad in (garbage, not_utf8):
            self.assertEqual(bad["error"]["code"], mcp.PARSE_ERROR)
            self.assertIsNone(bad["id"])
        self.assertEqual(call["id"], 7)

    def test_non_ascii_names_arrive_intact(self) -> None:
        (reply,) = self.serve(self.line(request("tools/call", name="app_usage", arguments={"query": "微信"})))
        result = json.loads(reply)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["matches"][0]["name"], "微信.exe")
        self.assertIn("微信.exe", result["content"][0]["text"])

    def test_a_lone_surrogate_does_not_stop_the_server(self) -> None:
        # A client may escape half of a surrogate pair; it comes back in the error text.
        surrogate = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"\\ud800","_meta":' \
                    + json.dumps(META).encode() + b"}}\n"
        first, second = self.serve(surrogate, self.line(request("tools/call", 2, name="list_days")))
        self.assertEqual(json.loads(first)["error"]["code"], mcp.INVALID_PARAMS)
        self.assertIn(b"\\ud800", first)
        self.assertEqual(json.loads(second)["id"], 2)

    def test_deep_nesting_is_a_parse_error_not_a_crash(self) -> None:
        deep = b"[" * 200_000 + b"]" * 200_000 + b"\n"
        first, second = self.serve(deep, self.line(request("tools/call", 2, name="list_days")))
        self.assertEqual(json.loads(first)["error"]["code"], mcp.PARSE_ERROR)
        self.assertEqual(json.loads(second)["id"], 2)

    def test_unicode_line_breaks_in_data_stay_inside_one_message(self) -> None:
        record(self.conn, datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc), 60,
               "odd\u2028na\u0085me\u2029.exe", "title")
        (reply,) = self.serve(self.line(request("tools/call", name="app_usage", arguments={"query": "odd"})))
        self.assertEqual(len(reply.decode("ascii").splitlines()), 1)
        match = json.loads(reply)["result"]["structuredContent"]["matches"][0]
        self.assertEqual(match["name"], "odd\u2028na\u0085me\u2029.exe")

    def test_a_client_that_went_away_ends_the_loop_quietly(self) -> None:
        # A closed pipe is BrokenPipeError on POSIX and a bare OSError(EINVAL) on Windows.
        for gone in (BrokenPipeError(), OSError(errno.EINVAL, "Invalid argument"), ConnectionResetError()):
            with self.subTest(error=type(gone).__name__):
                writes = []

                class Gone(io.BytesIO):
                    def write(self, data, error=gone):
                        writes.append(data)
                        raise error

                finished = self.server.serve(io.BytesIO(self.line(request("server/discover")) * 3), Gone())
                self.assertIs(finished, False)
                self.assertEqual(len(writes), 1)  # stopped at the first failure

    def test_serve_reports_a_normal_end_of_input(self) -> None:
        self.assertIs(self.server.serve(io.BytesIO(self.line(request("server/discover"))), io.BytesIO()), True)


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "ledger"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seed_ledger(self) -> None:
        conn = connect(build_default_config(self.data_dir).database_path)
        apply_migrations(conn)
        seed(conn)
        conn.close()

    def run_server(self, *flags: str, messages: tuple[dict, ...], cwd: Path = REPO_ROOT,
                   command: list[str] | None = None, env: dict[str, str] | None = None):
        environment = {**os.environ, "FOCUSLEDGER_DATA_DIR": str(self.data_dir),
                       "PYTHONPATH": str(REPO_ROOT), **(env or {})}
        done = subprocess.run(
            command or [sys.executable, "-m", "focusledger", "mcp", "serve", *flags],
            input="".join(json.dumps(m) + "\n" for m in messages).encode("utf-8"),
            capture_output=True, cwd=cwd, env=environment, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        return [json.loads(line) for line in done.stdout.splitlines()], done.stderr.decode("utf-8", "replace")

    def test_parser_accepts_the_mcp_commands(self) -> None:
        parser = build_parser()
        self.assertTrue(parser.parse_args(["mcp", "serve", "--include-titles"]).include_titles)
        self.assertFalse(parser.parse_args(["mcp", "serve"]).include_titles)
        self.assertIs(parser.parse_args(["mcp", "serve"]).func, mcp_cmd.cmd_serve)
        self.assertIs(parser.parse_args(["mcp", "tools"]).func, mcp_cmd.cmd_tools)

    def test_tools_command_lists_what_is_served(self) -> None:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            mcp_cmd.cmd_tools(build_parser().parse_args(["mcp", "tools"]))
        lines = printed.getvalue().splitlines()
        self.assertEqual(lines[0::2], [tool.name for tool in mcp.READ_TOOLS])
        self.assertTrue(all(line.startswith("  ") for line in lines[1::2]))
        self.assertNotIn("mark_absent", lines)

    def test_server_process_answers_over_pipes_and_keeps_stdout_for_the_protocol(self) -> None:
        self.seed_ledger()
        replies, stderr = self.run_server(messages=(
            request("server/discover", "d"),
            request("tools/list", 2),
            request("tools/call", 3, name="list_days"),
        ))
        self.assertEqual([reply["id"] for reply in replies], ["d", 2, 3])
        self.assertEqual(len(replies[1]["result"]["tools"]), 8)
        self.assertEqual(replies[2]["result"]["structuredContent"]["first_date"], DAY)
        self.assertIn("window titles withheld", stderr)

    def test_server_process_withholds_titles_unless_the_flag_is_given(self) -> None:
        self.seed_ledger()
        ask = (request("tools/call", 1, name="timeline", arguments={"date": DAY, "min_minutes": "1"}),
               request("tools/call", 2, name="day_summary", arguments={"date": DAY}))
        default, _ = self.run_server(messages=ask)
        sent = json.dumps(default, ensure_ascii=False)
        for word in TITLE_ONLY_WORDS:
            self.assertNotIn(word, sent)

        with_titles, stderr = self.run_server("--include-titles", messages=ask)
        sent = json.dumps(with_titles, ensure_ascii=False)
        self.assertIn("main.py - secret-project", sent)
        self.assertIn("narwhalcorp-internal.com", sent)
        self.assertIn("window titles included", stderr)

    def test_server_process_creates_nothing_when_there_is_no_ledger(self) -> None:
        replies, _ = self.run_server(messages=(
            request("tools/list", 1),
            request("tools/call", 2, name="list_days"),
        ))
        self.assertEqual(len(replies[0]["result"]["tools"]), 8)
        self.assertTrue(replies[1]["result"]["isError"])
        self.assertIn("focusledger init", replies[1]["result"]["content"][0]["text"])
        self.assertFalse(self.data_dir.exists())

    def test_stray_prints_go_to_stderr_not_into_the_protocol(self) -> None:
        class Stream:
            def __init__(self, data: bytes = b"") -> None:
                self.buffer = io.BytesIO(data)

        self.seed_ledger()

        def noisy_tool(ledger, name, arguments):
            print("STRAY PRINT")
            return {"ok": 1}

        sent = (json.dumps(request("tools/call", name="list_days")) + "\n").encode()
        fake_out, fake_err = Stream(), io.StringIO()
        args = build_parser().parse_args(["mcp", "serve"])
        with mock.patch.object(mcp_cmd, "load_config", return_value=build_default_config(self.data_dir)), \
                mock.patch.object(analyst, "run_tool", noisy_tool), \
                mock.patch.object(sys, "stdin", Stream(sent)), \
                mock.patch.object(sys, "stdout", fake_out), \
                mock.patch.object(sys, "stderr", fake_err):
            args.func(args)
            self.assertIs(sys.stdout, fake_out)  # put back when the server stops
        protocol = fake_out.buffer.getvalue()
        self.assertNotIn(b"STRAY", protocol)
        (reply,) = protocol.splitlines()
        self.assertEqual(json.loads(reply)["result"]["structuredContent"], {"ok": 1})
        self.assertIn("STRAY PRINT", fake_err.getvalue())

    def print_config(self, *flags: str, data_dir: str, cwd: Path) -> dict:
        done = subprocess.run(
            [sys.executable, "-m", "focusledger", "mcp", "config", *flags],
            capture_output=True, cwd=cwd, timeout=60,
            env={**os.environ, "FOCUSLEDGER_DATA_DIR": data_dir, "PYTHONPATH": str(REPO_ROOT)},
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        return json.loads(done.stdout)["mcpServers"]["focusledger"]

    def test_config_entry_launches_from_another_directory(self) -> None:
        """A client starts the server from its own directory, not from this repository."""
        self.seed_ledger()
        entry = self.print_config(data_dir=str(self.data_dir), cwd=REPO_ROOT)
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(entry["args"], ["-m", "focusledger", "mcp", "serve"])
        self.assertEqual(entry["env"]["FOCUSLEDGER_DATA_DIR"], os.path.abspath(self.data_dir))

        elsewhere = Path(self._tmp.name) / "client"
        elsewhere.mkdir()
        (reply,) = self.launch_entry(entry, elsewhere, request("tools/call", "c", name="list_days"))
        self.assertEqual(reply["result"]["structuredContent"]["first_date"], DAY)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_config_spells_out_a_relative_data_directory(self) -> None:
        project = Path(self._tmp.name) / "project"
        project.mkdir()
        entry = self.print_config(data_dir="mydata", cwd=project)
        printed = entry["env"]["FOCUSLEDGER_DATA_DIR"]
        self.assertTrue(os.path.isabs(printed))
        self.assertTrue(os.path.samefile(os.path.dirname(printed), project))
        self.assertEqual(os.path.basename(printed), "mydata")
        self.assertFalse((project / "mydata").exists())  # printing the entry creates nothing

    def launch_entry(self, entry: dict, cwd: Path, *messages: dict):
        """Start the printed entry the way a client would: its own directory, its own environment."""
        inherited = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "FOCUSLEDGER_DATA_DIR")}
        done = subprocess.run(
            [entry["command"], *entry["args"]],
            input="".join(json.dumps(m) + "\n" for m in messages).encode("utf-8"),
            capture_output=True, cwd=cwd, env={**inherited, **entry.get("env", {})}, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        return [json.loads(line) for line in done.stdout.splitlines()]

    def test_a_ledger_first_created_with_a_relative_data_directory_is_served(self) -> None:
        """Such a ledger has relative paths saved in config.json; they must not follow the client's cwd."""
        project = Path(self._tmp.name) / "project"
        ledger = project / "mydata"
        ledger.mkdir(parents=True)
        saved = build_default_config(Path("mydata")).to_json_dict()
        self.assertFalse(os.path.isabs(saved["database_path"]))
        (ledger / "config.json").write_text(json.dumps(saved), encoding="utf-8")
        conn = connect(ledger / "focusledger.sqlite3")
        apply_migrations(conn)
        seed(conn)
        conn.close()

        entry = self.print_config(data_dir="mydata", cwd=project)
        elsewhere = Path(self._tmp.name) / "client"
        elsewhere.mkdir()
        (reply,) = self.launch_entry(entry, elsewhere, request("tools/call", "c", name="list_days"))
        self.assertFalse(reply["result"]["isError"], reply["result"]["content"])
        self.assertEqual(reply["result"]["structuredContent"]["first_date"], DAY)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_an_unreadable_configuration_is_reported_to_the_client(self) -> None:
        self.data_dir.mkdir(parents=True)
        for broken in (
            "{not json",
            "[]",
            json.dumps({"log_level": "INFO"}),                        # no data_dir
            '{"data_dir": "x", "poll_interval_seconds": 1e999}',     # too large to be an int
            "[" * 200_000 + "]" * 200_000,                            # absurdly deep
        ):
            with self.subTest(config=broken[:40]):
                (self.data_dir / "config.json").write_text(broken, encoding="utf-8")
                replies, stderr = self.run_server(messages=(
                    request("tools/list", 1),
                    request("tools/call", 2, name="list_days"),
                ))
                self.assertEqual(len(replies[0]["result"]["tools"]), 8)
                self.assertTrue(replies[1]["result"]["isError"])
                self.assertIn("configuration", replies[1]["result"]["content"][0]["text"])
                self.assertNotIn(str(self.data_dir), json.dumps(replies))
                self.assertIn("configuration could not be read", stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual([p.name for p in self.data_dir.iterdir()], ["config.json"])

    def test_a_configuration_that_cannot_be_opened_is_reported_too(self) -> None:
        (self.data_dir / "config.json").mkdir(parents=True)  # a directory where the file should be
        replies, stderr = self.run_server(messages=(request("tools/call", 1, name="list_days"),))
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("configuration", replies[0]["result"]["content"][0]["text"])
        self.assertNotIn("Traceback", stderr)

    def start_server(self, **streams):
        process = subprocess.Popen(
            [sys.executable, "-m", "focusledger", "mcp", "serve"], cwd=REPO_ROOT,
            env={**os.environ, "FOCUSLEDGER_DATA_DIR": str(self.data_dir), "PYTHONPATH": str(REPO_ROOT)},
            **{"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, **streams},
        )
        # A regression here tends to show as a server that never answers, so
        # the process is ended after a while rather than left to hang the suite.
        watchdog = threading.Timer(90, process.kill)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        return process

    def test_server_process_answers_when_stderr_is_closed_or_missing(self) -> None:
        self.seed_ledger()
        ask = (json.dumps(request("tools/call", name="list_days")) + "\n").encode("utf-8")

        closed = self.start_server()
        closed.stderr.close()  # the host is not reading diagnostics
        closed.stdin.write(ask)
        closed.stdin.close()
        out = closed.stdout.read()
        closed.stdout.close()
        self.assertEqual(closed.wait(timeout=60), 0)
        self.assertEqual(json.loads(out)["result"]["structuredContent"]["first_date"], DAY)

        # With no stderr at all, print() would fall back to stdout and break the protocol.
        missing = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.stderr = None; from focusledger.cli import main; main(['mcp', 'serve'])"],
            input=ask, capture_output=True, cwd=REPO_ROOT, timeout=60,
            env={**os.environ, "FOCUSLEDGER_DATA_DIR": str(self.data_dir), "PYTHONPATH": str(REPO_ROOT)},
        )
        self.assertEqual(missing.returncode, 0)
        (only,) = missing.stdout.splitlines()
        self.assertEqual(json.loads(only)["result"]["structuredContent"]["first_date"], DAY)

    def test_server_process_exits_cleanly_when_the_client_closes_its_end(self) -> None:
        self.seed_ledger()
        process = self.start_server()
        try:
            ask = (json.dumps(request("tools/call", name="list_days")) + "\n").encode("utf-8")
            process.stdin.write(ask)
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["id"], 1)
            process.stdout.close()  # the client stops listening ...
            process.stdin.write(ask * 3)  # ... but something is still on its way
            process.stdin.close()
            stderr = process.stderr.read().decode("utf-8", "replace")
            self.assertEqual(process.wait(timeout=60), 0, stderr)
        finally:
            process.kill()
            process.wait()
            process.stderr.close()
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("Exception ignored", stderr)

    def test_config_carries_the_titles_flag(self) -> None:
        entry = self.print_config("--include-titles", data_dir=str(self.data_dir), cwd=REPO_ROOT)
        self.assertEqual(entry["args"], ["-m", "focusledger", "mcp", "serve", "--include-titles"])


if __name__ == "__main__":
    unittest.main()
