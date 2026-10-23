"""Ejecutable único ``satellite``: CLI (con TUI y MCP) y backend de la app de escritorio."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from satellite import launcher


def test_the_desktop_backend_subcommand_goes_to_the_desktop_main(monkeypatch) -> None:
    seen: list = []

    def fake_main(argv):
        seen.append(argv)
        return 0

    monkeypatch.setattr("satellite.desktop.__main__.main", fake_main)

    assert launcher.main(["desktop-backend", "--token-stdin", "--project", "/x"]) == 0
    assert seen == [["--token-stdin", "--project", "/x"]]


def test_everything_else_is_the_cli() -> None:
    with pytest.raises(SystemExit) as exit_info:
        launcher.main(["--help"])
    assert exit_info.value.code == 0


def test_the_cli_has_tui_and_mcp_commands() -> None:
    from satellite.cli.app import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "tui" in result.output and "mcp" in result.output


def test_satellite_mcp_serves_with_the_given_project(tmp_path, monkeypatch) -> None:
    import os

    from satellite.cli.app import app
    from satellite.mcp import server

    served: list = []
    monkeypatch.setattr(server.MCPServer, "serve", lambda self: served.append(True))
    monkeypatch.delenv("SATELLITE_MCP_PROJECT_ROOT", raising=False)

    result = CliRunner().invoke(app, ["mcp", "--project", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert served == [True]
    assert os.environ["SATELLITE_MCP_PROJECT_ROOT"] == str(tmp_path.resolve())


def test_the_console_script_is_declared() -> None:
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())
    assert data["project"]["scripts"]["satellite"] == "satellite.launcher:main"
