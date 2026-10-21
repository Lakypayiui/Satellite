"""The stdio LSP client against a fake language server."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from satellite.lsp.client import LspClient, LspError

FAKE_SERVER = textwrap.dedent(
    """
    import json
    import sys

    def read_message():
        headers = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            if line in (b"\\r\\n", b"\\n"):
                break
            key, _, value = line.decode().partition(":")
            headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        return json.loads(sys.stdin.buffer.read(length))

    def send(message):
        data = json.dumps(message).encode()
        sys.stdout.buffer.write(
            f"Content-Length: {len(data)}\\r\\n\\r\\n".encode() + data
        )
        sys.stdout.buffer.flush()

    while True:
        message = read_message()
        if message is None:
            break
        method = message.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
        elif method == "initialized":
            pass
        elif method == "textDocument/didOpen":
            uri = message["params"]["textDocument"]["uri"]
            send({
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": uri,
                    "diagnostics": [{
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "severity": 1,
                        "message": "boom",
                    }],
                },
            })
        elif method == "textDocument/didChange":
            pass
        elif method == "textDocument/definition":
            send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [{
                    "uri": message["params"]["textDocument"]["uri"],
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                }],
            })
        elif method == "textDocument/hover":
            send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"contents": {"kind": "markdown", "value": "docs"}},
            })
        elif method == "textDocument/references":
            send({"jsonrpc": "2.0", "id": message["id"], "result": []})
        elif method == "textDocument/documentSymbol":
            send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [{
                    "name": "f",
                    "kind": 12,
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 1, "character": 0},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                    "children": [],
                }],
            })
        elif method == "workspace/symbol":
            send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [{
                    "name": "f",
                    "kind": 12,
                    "location": {
                        "uri": message["params"]["query"],
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                    },
                }],
            })
        elif method == "test/slow":
            pass
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": message["id"], "result": None})
        elif method == "exit":
            break
        elif "id" in message:
            send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    """
)


def _server(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    target = tmp_path / "sample.py"
    target.write_text("x = 1\n", encoding="utf-8")
    return script, target


def test_client_diagnostics_and_navigation(tmp_path: Path) -> None:
    script, target = _server(tmp_path)

    async def scenario() -> None:
        client = LspClient(
            (__import__("sys").executable, str(script)),
            tmp_path,
            language_id="python",
        )
        await client.start()
        diagnostics, pending = await client.diagnostics(target, wait_s=2.0)
        assert pending is False
        assert diagnostics[0]["message"] == "boom"
        definition = await client.definition(target, 1, 1)
        assert definition[0]["uri"].endswith("sample.py")
        hover = await client.hover(target, 1, 1)
        assert hover["contents"]["value"] == "docs"
        assert await client.references(target, 1, 1) == []
        symbols = await client.document_symbols(target)
        assert symbols[0]["name"] == "f"
        workspace = await client.workspace_symbols(target.resolve().as_uri())
        assert workspace[0]["name"] == "f"
        await client.shutdown()

    asyncio.run(scenario())


def test_client_request_timeout(tmp_path: Path) -> None:
    script, _ = _server(tmp_path)

    async def scenario() -> None:
        client = LspClient(
            (__import__("sys").executable, str(script)),
            tmp_path,
            language_id="python",
        )
        await client.start()
        with pytest.raises(LspError) as info:
            await client.request("test/slow", {}, timeout=0.2)
        assert info.value.code == "lsp_timeout"
        await client.shutdown()

    asyncio.run(scenario())
