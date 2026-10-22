"""Server resolution and auto-install recipes (no network)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path

import pytest

from satellite.lsp.config import LspSettings, ServerOverride
from satellite.lsp.installer import (
    LspInstallError,
    _install_go,
    _install_npm,
    _jdtls_command,
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
    with pytest.raises(LspInstallError, match="traversal"):
        _safe_extract(archive, tmp_path / "out")


@pytest.mark.parametrize(
    "name",
    [
        "/tmp/evil",
        "C:/evil",
        "\\\\server\\share\\evil",
    ],
)
def test_safe_extract_rejects_absolute_and_drive_paths(
    tmp_path: Path, name: str
) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(LspInstallError):
        _safe_extract(archive, tmp_path / "out")


def test_safe_extract_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        tar.addfile(info)
    with pytest.raises(LspInstallError, match="enlace"):
        _safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "out" / "link").exists()


def test_safe_extract_rejects_hardlinks(tmp_path: Path) -> None:
    archive = tmp_path / "hardlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.LNKTYPE
        info.linkname = "target"
        tar.addfile(info)
    with pytest.raises(LspInstallError, match="enlace"):
        _safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "out" / "link").exists()


def test_safe_extract_writes_regular_files_and_keeps_exec_bit(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "ok.tar.gz"
    data = b"#!/bin/sh\nexit 0\n"
    with tarfile.open(archive, "w:gz") as tar:
        directory = tarfile.TarInfo("jdtls")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        tar.addfile(directory)
        script = tarfile.TarInfo("jdtls/bin/jdtls")
        script.mode = 0o755
        script.size = len(data)
        tar.addfile(script, io.BytesIO(data))

    destination = tmp_path / "out"
    _safe_extract(archive, destination)

    target = destination / "jdtls" / "bin" / "jdtls"
    assert target.read_bytes() == data
    if sys.platform != "win32":
        assert target.stat().st_mode & 0o111


def test_jdtls_state_key_uses_sha256(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    wrapper_name = "jdtls.bat" if os.name == "nt" else "jdtls"
    wrapper = lsp_root() / "jdtls" / "bin" / wrapper_name
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        "satellite.lsp.installer._java_available", lambda: True
    )
    monkeypatch.setattr(
        "satellite.lsp.installer.which", lambda name: None
    )

    command = _jdtls_command(tmp_path)

    assert command is not None
    digest = hashlib.sha256(
        str(tmp_path.resolve()).encode("utf-8")
    ).hexdigest()[:12]
    assert (lsp_root() / "state" / "jdtls" / digest).is_dir()
