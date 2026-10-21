"""The LSP tool's normalization, errors and path confinement."""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.lsp.uris import path_to_uri
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools import lsp as lsp_module
from satellite.tools.base import ToolContext
from satellite.tools.lsp import LspTool


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    async def diagnostics(self, path, *, language_id=None, wait_s=3.0):
        return (
            [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 3},
                    },
                    "severity": 1,
                    "message": "boom",
                }
            ],
            False,
        )

    async def definition(self, path, line, column, *, language_id=None):
        return [
            {
                "uri": path_to_uri(path),
                "range": {
                    "start": {"line": 2, "character": 4},
                    "end": {"line": 2, "character": 8},
                },
            }
        ]

    async def references(self, *args, **kwargs):
        return []

    async def hover(self, path, line, column, *, language_id=None):
        return {"contents": {"kind": "markdown", "value": "docs"}}

    async def document_symbols(self, path, *, language_id=None):
        return [
            {
                "name": "f",
                "kind": 12,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 1, "character": 0},
                },
                "children": [],
            }
        ]

    async def workspace_symbols(self, query):
        return []


class FakeManager:
    def __init__(self) -> None:
        self.client = FakeClient()

    async def ensure(self, language, **kwargs):
        return self.client

    def status(self):
        return {"root": "/tmp", "auto_install": True, "servers": []}

    async def install(self, language=None):
        return [{"id": "pyright", "ok": True, "command": ["pyright-langserver"]}]

    async def restart(self, language):
        return True


def _ctx(tmp_path: Path, policy=None) -> ToolContext:
    return ToolContext(project_root=tmp_path, policy=policy)


def _patch(monkeypatch, manager: FakeManager) -> None:
    monkeypatch.setattr(lsp_module, "manager_for", lambda root: manager)


def test_status(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, FakeManager())
    result = run(LspTool().execute({"action": "status"}, _ctx(tmp_path)))
    assert result.success is True
    assert result.output["auto_install"] is True


def test_install(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, FakeManager())
    result = run(
        LspTool().execute(
            {"action": "install", "language": "python"}, _ctx(tmp_path)
        )
    )
    assert result.success is True
    assert result.output["installed"][0]["ok"] is True


def test_diagnostics_are_1_based(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, FakeManager())
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    result = run(
        LspTool().execute(
            {"action": "diagnostics", "path": "a.py"}, _ctx(tmp_path)
        )
    )
    assert result.success is True
    item = result.output["diagnostics"][0]
    assert item["line"] == 1 and item["column"] == 1
    assert item["end_line"] == 1 and item["end_column"] == 4
    assert item["severity"] == "error"


def test_definition_and_hover(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, FakeManager())
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    definition = run(
        LspTool().execute(
            {
                "action": "definition",
                "path": "a.py",
                "line": 1,
                "column": 1,
            },
            _ctx(tmp_path),
        )
    )
    assert definition.success is True
    assert definition.output["locations"][0]["line"] == 3
    hover = run(
        LspTool().execute(
            {"action": "hover", "path": "a.py", "line": 1, "column": 1},
            _ctx(tmp_path),
        )
    )
    assert hover.output["text"] == "docs"


def test_external_definition_is_marked(monkeypatch, tmp_path: Path) -> None:
    class ExternalClient(FakeClient):
        async def definition(self, path, line, column, *, language_id=None):
            return [
                {
                    "uri": path_to_uri(tmp_path.parent / "lib.py"),
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                }
            ]

    manager = FakeManager()
    manager.client = ExternalClient()
    _patch(monkeypatch, manager)
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    result = run(
        LspTool().execute(
            {"action": "definition", "path": "a.py", "line": 1, "column": 1},
            _ctx(tmp_path),
        )
    )
    assert result.output["locations"][0]["external"] is True


def test_path_outside_project_is_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, FakeManager())
    policy = DefaultSecurityPolicy(allowed_tools={"lsp"})
    result = run(
        LspTool().execute(
            {"action": "diagnostics", "path": "../outside.py"},
            _ctx(tmp_path, policy),
        )
    )
    assert result.success is False
    assert result.error_code == "path_outside_project"


def test_unsupported_language(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, FakeManager())
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    result = run(
        LspTool().execute(
            {"action": "diagnostics", "path": "a.py", "language": "cobol"},
            _ctx(tmp_path),
        )
    )
    assert result.success is False
    assert result.error_code == "lsp_unsupported_language"
