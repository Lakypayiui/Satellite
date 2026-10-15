"""MCPServer: JSON-RPC 2.0 over stdio, dispatching MCP tools."""

from __future__ import annotations

import json
import sys
from typing import Any

from satellite.mcp.handlers import TOOLS_LIST, RuntimeBundle, get_handlers


class MCPServer:
    """Minimal MCP server (stdio JSON-RPC 2.0) with zero third-party deps."""

    def __init__(
        self,
        bundle: RuntimeBundle | None = None,
        stdin: Any = None,
        stdout: Any = None,
    ) -> None:
        self.bundle = bundle or RuntimeBundle()
        self.handlers = get_handlers(self.bundle)
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._initialized = False

    def _send(self, obj: dict) -> None:
        self._stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._stdout.flush()

    def dispatch(self, message: dict) -> dict | None:
        """Dispatch one JSON-RPC message; returns a response dict or None."""
        method = message.get("method")
        msg_id = message.get("id")

        if method == "initialize":
            self._initialized = True
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "satellite", "version": "0.2.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS_LIST},
            }
        if method == "tools/call":
            params = message.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            handler = self.handlers.get(name)
            if handler is None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {name}"},
                }
            return self._call_tool(msg_id, handler, arguments)
        if method and method.startswith("tools/"):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": msg_id if msg_id is not None else None,
            "error": {"code": -32600, "message": "invalid request"},
        }

    def _call_tool(self, msg_id, handler, arguments) -> dict:
        import asyncio

        try:
            result = asyncio.run(handler(arguments))
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": result.get("text", "")}],
                    "isError": result.get("isError", False),
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"runtime error: {exc}"}],
                    "isError": True,
                },
            }

    def serve(self) -> None:
        """Read lines from stdin, respond on stdout; clean exit on EOF."""
        while True:
            line = self._stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            response = self.dispatch(message)
            if response is not None:
                self._send(response)
