"""A small MCP client over stdio, enough to drive FocusLedger's own server.

The analyst calls its tools in-process. This client exists so the same tools
can be exercised the way an outside agent reaches them: through a separate
server process, over the protocol, with whatever the server withholds
withheld. ``analyst bench --via-mcp`` uses it to run a local model against the
server; nothing else in FocusLedger depends on it.

It speaks the stateless 2026-07-28 revision: every request carries the
protocol version and the client's capabilities, and there is no handshake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import focusledger
from focusledger.services import mcp

_META = {
    "io.modelcontextprotocol/protocolVersion": mcp.MODERN_VERSIONS[0],
    "io.modelcontextprotocol/clientCapabilities": {},
}


class McpError(Exception):
    """The server went away, or answered with something that is not a reply."""


class McpClient:
    """One server process and a line-by-line conversation with it."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None) -> None:
        self._next_id = 0
        self._process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        """End the server by closing its input, as a client that quits would."""
        process = self._process
        for stream in (process.stdin, process.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def request(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """Send one request and return the whole reply, result or error."""
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method,
                   "params": {**(params or {}), "_meta": _META}}
        try:
            self._process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
            self._process.stdin.flush()
            line = self._process.stdout.readline()
        except (OSError, ValueError) as exc:
            raise McpError(f"the MCP server cannot be reached: {exc}") from exc
        if not line:
            raise McpError("the MCP server closed the connection")
        try:
            reply = json.loads(line)
        except ValueError as exc:
            raise McpError("the MCP server sent a line that is not JSON") from exc
        if not isinstance(reply, dict) or reply.get("id") != self._next_id:
            raise McpError("the MCP server sent a reply to a different request")
        return reply

    def result(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """The result of a request that is expected to succeed."""
        reply = self.request(method, params)
        if "result" not in reply:
            raise McpError(f"{method} failed: {reply.get('error')}")
        return reply["result"]  # type: ignore[return-value]

    def list_tools(self) -> list[dict[str, object]]:
        return self.result("tools/list")["tools"]  # type: ignore[return-value]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """A tool's result in the shape the analyst's own tools return.

        A refusal of any kind (a tool the server does not offer, arguments it
        rejects, a ledger it cannot open) comes back as ``{"error": ...}``, so
        a model reads it the way it reads a failed call of an in-process tool.
        """
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        if "result" not in reply:
            error = reply.get("error")
            return {"error": str(error.get("message") if isinstance(error, dict) else error)}
        result = reply["result"]
        text = " ".join(str(part.get("text", "")) for part in result.get("content") or [])
        if result.get("isError"):
            return {"error": text}
        structured = result.get("structuredContent")
        return structured if isinstance(structured, dict) else json.loads(text)

    def run_tool(self, ledger, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Stands in for ``analyst.run_tool``: the ledger argument is not used,
        because over MCP the server reads the database, not the caller."""
        return self.call_tool(name, arguments)


def start_server(data_dir: Path | str | None = None, include_titles: bool = False) -> McpClient:
    """Start ``focusledger mcp serve`` with this interpreter and connect to it.

    ``data_dir`` chooses the ledger the server reads; without it the server
    uses the one this process would.
    """
    command = [sys.executable, "-m", "focusledger", "mcp", "serve"]
    if include_titles:
        command.append("--include-titles")
    env = dict(os.environ)
    if data_dir is not None:
        env["FOCUSLEDGER_DATA_DIR"] = str(data_dir)
    # So that ``-m focusledger`` resolves from a source checkout as well.
    source_root = str(Path(focusledger.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(part for part in (source_root, env.get("PYTHONPATH")) if part)
    return McpClient(command, env)
