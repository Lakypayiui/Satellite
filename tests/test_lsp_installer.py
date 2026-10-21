"""Server resolution and auto-install recipes (no network)."""

from __future__ import annotations

import asyncio
import io
import sys
import tarfile
from pathlib import Path

import pytest

from satellite.lsp.config import LspSettings, ServerOverride
from satellite.lsp.installer import (
    LspInstallError,
    _install_go,
    _install_npm,
    _safe_extract,
    lsp_root,
    resolve_command,
)
from satellite.lsp.servers import SERVERS


def run(coro):
    return asyncio.run(coro)


def _home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))


def test_custom_override_wins(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    settings = LspSettings(
        servers={
            "python": ServerOverride(
                command=(sys.executable, "-c", "pass"),
            )
        }
    )
    command = resolve_command("pyright", project_root=tmp_path, settings=settings)
    assert command == (sys.executable, "-c", "pass")


def test_local_npm_binary_is_resolved(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    binary = lsp_root() / "npm" / "node_modules" / ".bin" / "pyright-langserver"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    command = resolve_command("pyright", project_root=tmp_path)
    assert command == (str(binary), "--stdio")


def test_npm_recipe_uses_local_prefix(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "satellite.lsp.installer.which",
        lambda name: "/usr/bin/npm" if name == "npm" else None,
    )

    async def fake_run(argv, *, timeout, env_extra=None):
        calls.append(list(argv))
        return 0, "", ""

    monkeypatch.setattr("satellite.lsp.installer._run", fake_run)
    result = run(_install_npm(SERVERS["pyright"], 10))
    assert result.ok is True
    assert "--prefix" in calls[0]
    assert str(lsp_root() / "npm") in calls[0]
    assert "pyright" in calls[0]


def test_go_recipe_sets_gobin(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    captured: dict = {}

    monkeypatch.setattr(
        "satellite.lsp.installer.which",
        lambda name: "/usr/bin/go" if name == "go" else None,
    )

    async def fake_run(argv, *, timeout, env_extra=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env_extra or {})
        return 0, "", ""

    monkeypatch.setattr("satellite.lsp.installer._run", fake_run)
    result = run(_install_go(SERVERS["gopls"], 10))
    assert result.ok is True
    caught_gobin = captured["env"]["GOBIN"]
    assert caught_gobin
    assert caught_gobin.endswith(str(Path("go") / "bin"))
    assert "golang.org/x/tools/gopls@latest" in captured["argv"]


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../evil")
        data = b"x"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(LspInstallError):
        _safe_extract(archive, tmp_path / "out")
