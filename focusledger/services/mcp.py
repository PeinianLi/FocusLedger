"""Model Context Protocol server: the analyst's read tools for other clients.

FocusLedger's own analyst calls these tools with a local model. This module
serves the same tools over MCP's stdio transport, so any MCP client can ask
about tracked time. Three rules shape it:

- Read only. The two tools that propose changes are not served: a proposal
  needs the user's approval inside FocusLedger, and an outside client's model
  must not be the one to give it. The database file is opened read-only, so
  the server cannot create it, upgrade it or change a row in it.
- Window titles stay behind unless asked for. An MCP client may run a cloud
  model, which FocusLedger never does on its own, and titles are the most
  personal thing the tools return. That covers what is read out of a title
  too: a site is named only if it is on the built-in list of well-known
  sites, because any other "site" is a piece of title text.
- Stateless. Every call opens the database and computes from scratch, as the
  2026-07-28 revision requires; nothing is inferred from earlier requests.

Both protocol eras are served: per-request metadata (2026-07-28) and the
``initialize`` handshake of the revisions before it, which many clients still
use. Only the standard library is involved, like the rest of FocusLedger.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from sqlite3 import Connection
from typing import BinaryIO, Callable
from urllib.parse import quote

from focusledger import __version__
from focusledger.db.migrations import SCHEMA_VERSION
from focusledger.services import analyst
from focusledger.services.classify import well_known_sites

logger = logging.getLogger(__name__)

SERVER_NAME = "focusledger"
# Revisions that carry version and capabilities on every request.
MODERN_VERSIONS = ("2026-07-28",)
# Revisions that open with an ``initialize`` handshake, newest first.
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_VERSIONS = (*MODERN_VERSIONS, *LEGACY_VERSIONS)
# The one revision in which a client may send several messages as a JSON array.
BATCH_VERSION = "2025-03-26"

_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

# The tool list is fixed for the life of the process and holds no user data.
LIST_TTL_MS = 3_600_000
# Keys whose values are window titles, wherever they appear in a tool result.
TITLE_KEYS = frozenset({"main_title"})
# What the client is told when titles are withheld. The wording is fixed and
# says nothing about the data, so it cannot be used to learn what was hidden.
_TITLES_WITHHELD = " This server withholds window titles."
_SITES_GROUPED = (
    " This server names only a built-in list of well-known sites; time on any "
    f'other site is counted together as "{analyst.OTHER_SITE}" and cannot be '
    "found by name."
)
_WITHHELD_NOTICES = {
    "timeline": _TITLES_WITHHELD,
    "day_summary": _SITES_GROUPED,
    "app_usage": _SITES_GROUPED,
}
# Replaces the analyst's own "nothing matches" note, which would be untrue
# here: a site outside the built-in list is hidden, not absent.
_NO_MATCH_NOTE = (
    "No app or named site contains this text in the range. Sites outside the "
    "built-in list are not named by this server, so this does not show that no "
    "time was spent on such a site. This tool matches names only; for a "
    "category such as gaming use range_summary or compare_ranges."
)

INSTRUCTIONS = (
    "Read-only access to the user's FocusLedger attention ledger: which apps and "
    "categories took their time and how much of it was engaged. Call list_days "
    "first to learn which dates exist. Every figure is already computed; copy "
    "numbers from tool results in the same unit and do not calculate new ones. "
    "The data is personal: use it only to answer the user's own question."
)
_INSTRUCTIONS_WITHHELD = (
    " Window titles are withheld, and only well-known sites are named; the rest "
    f'appear together as "{analyst.OTHER_SITE}".'
)

READ_TOOLS: tuple[analyst.Tool, ...] = tuple(
    tool for tool in analyst.TOOLS if tool.name not in analyst.WRITE_TOOLS
)
_READ_TOOLS_BY_NAME = {tool.name: tool for tool in READ_TOOLS}


class LedgerUnavailable(Exception):
    """The database cannot be served as it is; the message tells the user what to do."""


class _RpcError(Exception):
    """A request that ends in a JSON-RPC error rather than a result."""

    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def tool_definitions(include_titles: bool = False) -> list[dict[str, object]]:
    """The served tools in MCP's shape, in a stable order."""
    return [
        {
            "name": tool.name,
            "description": tool.description
            + ("" if include_titles else _WITHHELD_NOTICES.get(tool.name, "")),
            "inputSchema": tool.parameters,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        }
        for tool in READ_TOOLS
    ]


def read_only_uri(database_path) -> str:
    """A SQLite URI that opens ``database_path`` read-only, wherever it lives.

    SQLite accepts no host in a file URI, so the whole path goes into the path
    part: ``file:///C:/dir/db`` for a drive and ``file:////server/share/db``
    for a network share. An extended-length ``\\\\?\\`` prefix is kept, since it
    is there for paths that do not open without it. The path is made absolute
    without following links.
    """
    text = os.path.abspath(database_path)
    if os.name == "nt":
        # Only Windows spells separators as backslashes; elsewhere a backslash
        # is an ordinary character in a name and must survive as it is.
        text = text.replace("\\", "/")
        if not text.startswith("/"):
            text = "/" + text
    return "file://" + quote(text, safe="/:") + "?mode=ro"


def open_read_only(database_path) -> Connection:
    """Open an existing, current database without being able to change it.

    The file is opened in SQLite's read-only mode, so nothing here can create
    it, switch its journal mode or write a row; ``query_only`` is a second
    guard on the same connection. A database that is missing or on another
    schema version is refused rather than created or upgraded, because both
    would be writes.

    One thing SQLite still does on its own: reading a database that is in WAL
    mode needs the ``-wal`` and ``-shm`` companion files, and it creates them
    beside the database if they are not there. The database file itself is
    not touched.
    """
    path = Path(database_path)
    if not path.is_file():
        raise LedgerUnavailable(
            "There is no FocusLedger database yet. Run `python -m focusledger init` and "
            "start collecting, then ask again."
        )
    try:
        conn = sqlite3.connect(read_only_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise LedgerUnavailable("The FocusLedger database could not be opened for reading.") from exc
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        conn.close()
        raise LedgerUnavailable("The FocusLedger database could not be read.") from exc
    if version != SCHEMA_VERSION:
        conn.close()
        if version > SCHEMA_VERSION:
            raise LedgerUnavailable(
                "The FocusLedger database is newer than this server. Update FocusLedger, "
                "then restart the MCP server."
            )
        raise LedgerUnavailable(
            "The FocusLedger database needs an upgrade, which this read-only server will "
            "not perform. Run `python -m focusledger status` once, then ask again."
        )
    return conn


class McpServer:
    """Answers MCP messages one at a time; holds no per-conversation state."""

    def __init__(
        self,
        config,
        include_titles: bool = False,
        open_connection: Callable[[], Connection] | None = None,
    ) -> None:
        self._config = config
        self._include_titles = include_titles
        self._open = open_connection or (lambda: open_read_only(config.database_path))
        self._public_sites = None if include_titles else well_known_sites()
        # Set by ``initialize``. The handshake is scoped to the stdio process,
        # so it is the one thing remembered between requests, and only for
        # clients that speak a revision which requires it.
        self._legacy_version: str | None = None

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def serve(self, stdin: BinaryIO, stdout: BinaryIO) -> bool:
        """Newline-delimited JSON-RPC until the client closes stdin or goes away.

        Returns False if it stopped because replies could no longer be written.
        """
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            reply = self.handle_line(line)
            if reply is None:
                continue
            try:
                stdout.write(_encode(reply) + b"\n")
                stdout.flush()
            except OSError:
                # The client is gone and nobody is left to answer. A closed
                # pipe is BrokenPipeError on POSIX but a bare OSError(EINVAL)
                # on Windows, so the base class is what has to be caught.
                return False
        return True

    def handle_line(self, line: bytes | str) -> dict[str, object] | list[object] | None:
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            # ValueError covers malformed JSON and bytes that are not UTF-8;
            # RecursionError is what absurdly deep nesting raises instead.
            return _error(None, PARSE_ERROR, "Parse error: the message is not valid JSON")
        return self.handle_message(message)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def handle_message(self, message: object) -> dict[str, object] | list[object] | None:
        if isinstance(message, list):
            return self._handle_batch(message)
        return self._handle_one(message)

    def _handle_batch(self, messages: list[object]) -> dict[str, object] | list[object] | None:
        # Arrays exist in exactly one revision, and only after its handshake.
        if self._legacy_version != BATCH_VERSION or not messages:
            return _error(None, INVALID_REQUEST,
                          "Invalid request: expected one JSON-RPC request or notification")
        replies = [
            reply
            for reply in (self._handle_one(message, in_batch=True) for message in messages)
            if reply is not None
        ]
        return replies or None  # a batch of notifications gets no reply at all

    def _handle_one(self, message: object, in_batch: bool = False) -> dict[str, object] | None:
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            request_id = message.get("id") if isinstance(message, dict) else None
            return _error(_valid_id(request_id), INVALID_REQUEST,
                          "Invalid request: expected one JSON-RPC request or notification")
        if "id" not in message:
            # A notification (initialized, cancelled, ...) never gets a reply.
            # Calls here are short and synchronous, so there is nothing to cancel.
            return None
        request_id = message["id"]
        if _valid_id(request_id) is None:
            return _error(None, INVALID_REQUEST, "Invalid request: id must be a string or an integer")

        method = str(message["method"])
        try:
            params = message.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise _RpcError(INVALID_PARAMS, "params must be an object")
            if method == "initialize":
                if in_batch:
                    raise _RpcError(INVALID_REQUEST, "initialize must not be part of a batch")
                return _result(request_id, self._initialize(params))
            modern = self._era(method, params)
            result = self._call(method, params, modern)
        except _RpcError as exc:
            return _error(request_id, exc.code, exc.message, exc.data)
        except Exception:  # noqa: BLE001 - a bug here must not take the server down
            logger.exception("MCP request %s failed", method)
            return _error(request_id, INTERNAL_ERROR, "Internal error; see the server log")

        if modern:
            result = {
                "resultType": "complete",
                **result,
                "_meta": {_SERVER_INFO: _server_info()},
            }
        return _result(request_id, result)

    def _era(self, method: str, params: dict[str, object]) -> bool:
        """True for a request carrying its own metadata, False after a handshake."""
        meta = params.get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        requested = meta.get(_PROTOCOL_VERSION)
        if requested is not None:
            if not isinstance(requested, str):
                raise _RpcError(INVALID_PARAMS, f"{_PROTOCOL_VERSION} must be a string")
            if requested not in MODERN_VERSIONS:
                # The full list, as server/discover gives it: a client that
                # also knows a handshake revision can fall back to initialize.
                raise _RpcError(
                    UNSUPPORTED_PROTOCOL_VERSION,
                    "Unsupported protocol version",
                    {"supported": list(SUPPORTED_VERSIONS), "requested": requested},
                )
            if not isinstance(meta.get(_CLIENT_CAPABILITIES), dict):
                raise _RpcError(
                    INVALID_PARAMS,
                    f"Missing required _meta field: {_CLIENT_CAPABILITIES}",
                )
            return True
        if self._legacy_version is not None or method == "ping":
            return False
        raise _RpcError(
            INVALID_PARAMS,
            f"Missing required _meta field: {_PROTOCOL_VERSION}. This server speaks "
            f"{', '.join(MODERN_VERSIONS)} with per-request metadata, and "
            f"{', '.join(LEGACY_VERSIONS)} after an initialize handshake.",
        )

    def _call(self, method: str, params: dict[str, object], modern: bool) -> dict[str, object]:
        if method == "server/discover":
            return {
                "supportedVersions": list(SUPPORTED_VERSIONS),
                "capabilities": _capabilities(),
                "instructions": self._instructions(),
                "ttlMs": LIST_TTL_MS,
                "cacheScope": "public",
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            listing: dict[str, object] = {"tools": tool_definitions(self._include_titles)}
            if modern:
                listing.update(ttlMs=LIST_TTL_MS, cacheScope="public")
            return listing
        if method == "tools/call":
            return self._call_tool(params)
        raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    # ------------------------------------------------------------------
    # methods
    # ------------------------------------------------------------------
    def _initialize(self, params: dict[str, object]) -> dict[str, object]:
        requested = params.get("protocolVersion")
        # Echo a revision we speak; otherwise offer our newest and let the
        # client decide whether it can continue.
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        self._legacy_version = str(version)
        return {
            "protocolVersion": self._legacy_version,
            "capabilities": _capabilities(),
            "serverInfo": _server_info(),
            "instructions": self._instructions(),
        }

    def _instructions(self) -> str:
        return INSTRUCTIONS + ("" if self._include_titles else _INSTRUCTIONS_WITHHELD)

    def _call_tool(self, params: dict[str, object]) -> dict[str, object]:
        name = params.get("name")
        if not isinstance(name, str) or name not in _READ_TOOLS_BY_NAME:
            # Covers the proposal tools too: over MCP they do not exist.
            raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _RpcError(INVALID_PARAMS, "arguments must be an object")

        try:
            with closing(self._open()) as conn:
                ledger = analyst.Ledger(conn, self._config, public_sites=self._public_sites)
                output = analyst.run_tool(ledger, name, arguments)
        except LedgerUnavailable as exc:
            # Something the user can fix, so it is said in words the model can pass on.
            return {"content": [_text(str(exc))], "isError": True}
        if "error" in output:
            # Bad arguments are for the model to fix, so they travel as a tool
            # result it can read, not as a protocol error.
            return {"content": [_text(str(output["error"]))], "isError": True}
        if not self._include_titles:
            output = _without_titles(output)
            if name == "app_usage" and not output.get("matches"):
                # The same words for every empty result, whatever was asked.
                output = {**output, "note": _NO_MATCH_NOTE}
        return {
            # allow_nan=False: NaN and Infinity are not JSON, so a result that
            # holds one is a bug to report, not a line to send.
            "content": [_text(json.dumps(output, ensure_ascii=False, allow_nan=False))],
            "structuredContent": output,
            "isError": False,
        }


def _encode(reply: object) -> bytes:
    """One message as one line of ASCII.

    Escaping everything outside ASCII keeps the framing safe whatever the data
    holds: a lone surrogate from a client's ``\\ud800`` cannot fail to encode,
    and U+2028 or U+0085 from a window title cannot be taken for a line break.
    """
    return json.dumps(reply, ensure_ascii=True, allow_nan=False).encode("ascii")


def _capabilities() -> dict[str, object]:
    return {"tools": {"listChanged": False}}


def _server_info() -> dict[str, str]:
    return {"name": SERVER_NAME, "version": __version__}


def _text(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def _without_titles(value):
    if isinstance(value, dict):
        return {k: _without_titles(v) for k, v in value.items() if k not in TITLE_KEYS}
    if isinstance(value, list):
        return [_without_titles(item) for item in value]
    return value


def _valid_id(value: object) -> str | int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (str, int)) else None


def _result(request_id: object, result: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str, data: object = None) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
