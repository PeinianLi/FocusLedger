"""`focusledger mcp ...` — serve the analyst's read tools to MCP clients."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import focusledger
from focusledger.config import AppConfig, build_default_config, config_path, load_config
from focusledger.services import mcp

_TITLES_HELP = (
    "Also return window titles and every site name. Off by default, because an MCP "
    "client may send tool results to a cloud model."
)
_CLIENT_WARNING = (
    "Note: this gives the client's model read access to your tracked time. FocusLedger itself "
    "keeps everything on this machine, but a client that uses a cloud model sends each tool "
    "result to that provider, and the server cannot tell which kind of client it is talking to. "
    "Connect a client that runs a local model unless you accept that. The server offers read "
    "tools only; changes can be proposed only by the analyst inside FocusLedger."
)
_UNREADABLE_CONFIG = (
    "The FocusLedger configuration file could not be read. Fix or delete config.json "
    "in the FocusLedger data directory, then restart the MCP server."
)


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "mcp", help="Serve your tracked time to MCP clients, read-only."
    )
    sub = parser.add_subparsers(required=True)

    serve = sub.add_parser(
        "serve", help="Run the MCP server on stdin/stdout. An MCP client starts this for you."
    )
    serve.add_argument("--include-titles", action="store_true", help=_TITLES_HELP)
    serve.set_defaults(func=cmd_serve)

    config = sub.add_parser(
        "config", help="Print the entry to paste into an MCP client's configuration."
    )
    config.add_argument("--include-titles", action="store_true", help=_TITLES_HELP)
    config.set_defaults(func=cmd_config)

    tools = sub.add_parser("tools", help="List the tools served over MCP.")
    tools.set_defaults(func=cmd_tools)


def _say(text: str) -> None:
    """A line for whoever started the server, on stderr or not at all.

    With no stderr, ``print`` would fall back to stdout and put the line into
    the protocol stream; with a closed one it would raise before the first
    request is read. A status line is worth neither.
    """
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(text, file=stream, flush=True)
    except (OSError, ValueError):
        # Nobody is reading. The line is still buffered and would fail again
        # when the interpreter flushes at exit, so it is dropped for good.
        _discard_unsent_output(stream)


def cmd_serve(args: argparse.Namespace) -> None:
    server = _build_server(args.include_titles)
    titles = "included" if args.include_titles else "withheld"
    _say(
        f"FocusLedger MCP server: {len(mcp.READ_TOOLS)} read-only tools, window titles "
        f"{titles}. Waiting for a client on stdin."
    )
    # From here on stdout carries protocol messages and nothing else, so
    # anything printed by accident is sent to stderr instead.
    protocol_in, protocol_out = sys.stdin.buffer, sys.stdout.buffer
    printed_to = sys.stdout
    sys.stdout = sys.stderr
    try:
        client_still_there = server.serve(protocol_in, protocol_out)
    except KeyboardInterrupt:
        client_still_there = True
    finally:
        sys.stdout = printed_to
    if not client_still_there:
        _discard_unsent_output(printed_to)


def _build_server(include_titles: bool) -> mcp.McpServer:
    """A server for the configured ledger, or one that explains why there is none.

    The configuration is only read. Unlike other commands this one does not
    create the data directory or upgrade the database: it is the read-only
    door, and whatever is wrong with the ledger is reported to the client as a
    tool result instead of ending the process before it can say anything.
    """
    try:
        return mcp.McpServer(_serving_config(), include_titles=include_titles)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        # An unreadable file, malformed JSON, a missing key, a value of the
        # wrong type, a number too large to convert, absurdly deep nesting.
        _say(
            f"FocusLedger configuration could not be read ({type(exc).__name__}); "
            "tool calls will say so."
        )

        def unreadable():
            raise mcp.LedgerUnavailable(_UNREADABLE_CONFIG)

        return mcp.McpServer(
            build_default_config(_config_dir()),
            include_titles=include_titles,
            open_connection=unreadable,
        )


def _serving_config() -> AppConfig:
    cfg = load_config()
    if cfg.database_path.is_absolute():
        return cfg
    # A ledger first created with a relative FOCUSLEDGER_DATA_DIR has relative
    # paths saved in config.json, which mean something else from the client's
    # working directory. The database sits beside the configuration file.
    return replace(cfg, database_path=_config_dir() / cfg.database_path.name)


def _config_dir() -> Path:
    """The data directory in use, absolute, without following links."""
    return Path(os.path.abspath(config_path().parent))


def _discard_unsent_output(stream) -> None:
    """After the client has gone, keep interpreter shutdown from failing.

    The reply that could not be delivered is still buffered. Flushing it at
    exit would fail again, print an "Exception ignored" message and turn the
    exit code into 120, so the descriptor is pointed at the null device.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, stream.fileno())
        finally:
            os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass  # not a real file (a test double, or already closed)


def cmd_config(args: argparse.Namespace) -> None:
    command = ["-m", "focusledger", "mcp", "serve"]
    if args.include_titles:
        command.append("--include-titles")
    entry: dict[str, object] = {"command": sys.executable, "args": command}
    env = _launch_env()
    if env:
        entry["env"] = env
    print(json.dumps({"mcpServers": {mcp.SERVER_NAME: entry}}, indent=2))
    # On stderr, so the JSON on stdout can still be piped into a file.
    _say(_CLIENT_WARNING)


def _launch_env() -> dict[str, str]:
    """What the client must set so the server starts from any directory."""
    env: dict[str, str] = {}
    if os.environ.get("FOCUSLEDGER_DATA_DIR"):
        # The directory in use now, spelled out in full: the client starts the
        # server from its own working directory, where a relative path would
        # point somewhere else.
        env["FOCUSLEDGER_DATA_DIR"] = str(_config_dir())
    try:
        importlib.metadata.distribution("focusledger")
    except importlib.metadata.PackageNotFoundError:
        # Running from a source checkout: ``-m focusledger`` would not resolve
        # from the client's working directory either.
        env["PYTHONPATH"] = str(Path(focusledger.__file__).resolve().parents[1])
    return env


def cmd_tools(args: argparse.Namespace) -> None:
    for tool in mcp.tool_definitions():
        print(tool["name"])
        print(f"  {tool['description']}")
