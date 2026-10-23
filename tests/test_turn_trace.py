"""Fase C del plan de correcciones (docs/PLAN-correcciones.md), punto 9.

Sin planner no hay grafo que guardar: cada turno guarda su **traza** (tools del piloto y
delegaciones) y las superficies la muestran en lugar del grafo.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.orchestration.chat import ChatSession
from satellite.persistence.store import FileStore
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def _runtime(tmp_path: Path, monkeypatch, script: list[dict]):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"name": "fake", "model": "m"}), encoding="utf-8")
    provider = FakeLLM(script=script, default_content="")
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: FakeLLM(default_content="r") if role == "context" else default,
    )
    orchestrator, _bus, _store, _registry = runtime_builder.build_runtime(project)
    return project, orchestrator


def _call(call_id: str, name: str, **arguments) -> dict:
    return {"tool_calls": [{"id": call_id, "name": name, "arguments": arguments}]}


def _turn_with_a_delegation(tmp_path: Path, monkeypatch):
    coder_output = {
        "summary": "Hecho.",
        "files_changed": ["auth.py"],
        "verification": {"command": "pytest", "passed": True, "details": "1 passed"},
    }
    project, orchestrator = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call("c1", "read_file", path="auth.py"),
            _call("c2", "delegate", agent_id="python_coder", task={"goal": "edit auth.py"}),
            {"content": json.dumps(coder_output)},
            {"content": "Listo."},
        ],
    )
    reply = run(ChatSession(project, orchestrator).send("arregla el login"))
    return project, reply


def test_a_turn_saves_its_trace(tmp_path: Path, monkeypatch) -> None:
    project, reply = _turn_with_a_delegation(tmp_path, monkeypatch)

    state = run(FileStore(project).load_state(reply.task_id))

    assert state["status"] == "ok"
    trace = state["trace"]
    assert [item["tool"] for item in trace["pilot_tools"]] == ["read_file", "delegate"]
    assert all(item["ok"] for item in trace["pilot_tools"])
    [delegation] = trace["delegations"]
    assert delegation["agent_id"] == "python_coder"
    assert delegation["status"] == "ok"
    assert "tokens_in" in delegation and "files_touched" in delegation


def test_the_cli_inspect_shows_the_trace(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from satellite.cli.app import app

    project, reply = _turn_with_a_delegation(tmp_path, monkeypatch)

    result = CliRunner().invoke(app, ["inspect", reply.task_id, "--project", str(project)])

    assert result.exit_code == 0, result.output
    assert "python_coder" in result.output
    assert "read_file" in result.output


def test_mcp_trace_tool_shows_the_trace(tmp_path: Path, monkeypatch) -> None:
    from satellite.mcp.handlers import TOOLS_LIST, get_handlers

    project, reply = _turn_with_a_delegation(tmp_path, monkeypatch)

    class _Bundle:
        _active: dict = {}

        def get(self, _root):
            return {"store": FileStore(project)}

    names = {tool["name"] for tool in TOOLS_LIST}
    assert "satellite_trace" in names and "satellite_graph" not in names
    out = run(get_handlers(_Bundle())["satellite_trace"]({"task_id": reply.task_id}))
    assert out["isError"] is False
    assert "python_coder" in out["text"]


def test_the_desktop_task_detail_carries_the_trace(tmp_path: Path, monkeypatch) -> None:
    from satellite.desktop.backend import DesktopBackend

    project, reply = _turn_with_a_delegation(tmp_path, monkeypatch)
    backend = DesktopBackend.__new__(DesktopBackend)
    backend.resolve = lambda _root=None: project  # type: ignore[method-assign]

    detail = run(backend.task_detail(reply.task_id))

    assert detail["trace"]["delegations"][0]["agent_id"] == "python_coder"
    assert "graph" not in detail and "graph_error" not in detail


def test_a_task_without_trace_says_so(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from satellite.cli.app import app

    result = CliRunner().invoke(app, ["inspect", "no-existe", "--project", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "sin traza" in result.output
