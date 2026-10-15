"""Tests de la CLI (con runtime builder monkeypatched)."""

from pathlib import Path

from typer.testing import CliRunner

from satellite.cli.app import app
from satellite.core.task import TaskResult, TaskStatus


def test_tui_command_says_what_is_missing(monkeypatch) -> None:
    """Sin el paquete de la TUI, el CLI lo explica en vez de soltar el traceback."""
    import sys

    monkeypatch.setitem(sys.modules, "satellite.tui.app", None)  # simula que falta
    runner = CliRunner()
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 1
    assert "textual" in result.output
    assert "pip install" in result.output


def test_agents_command(tmp_path: Path, monkeypatch) -> None:
    """agents lista el catálogo sin red ni LLM real."""

    class FakeRegistry:
        def all(self):
            from satellite.core.spec import AgentSpec

            return [
                AgentSpec(id="html_writer", name="W", capabilities=("write_html",), prompt_ref="p"),
                AgentSpec(id="test_runner", name="T", capabilities=("run_tests",), prompt_ref="p"),
            ]

    def fake_build(project):
        return None, None, None, FakeRegistry()

    monkeypatch.setattr("satellite.cli.app.build_runtime", fake_build)
    runner = CliRunner()
    result = runner.invoke(app, ["agents", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert "html_writer" in result.output
    assert "test_runner" in result.output


def test_run_command_with_fake_orchestrator(tmp_path: Path, monkeypatch) -> None:
    """run con orchestrator fake → exit 0 y resumen."""

    class FakeOrch:
        async def execute(self, task):
            return TaskResult(task_id="x", status=TaskStatus.OK, summary="tarea ok", output={})

    def fake_build(project):
        return FakeOrch(), None, None, None

    monkeypatch.setattr("satellite.cli.app.build_runtime", fake_build)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "crear html", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert "tarea ok" in result.output
    assert "ok" in result.output
