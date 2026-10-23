"""Fase D del plan de correcciones (docs/PLAN-correcciones.md), punto 11.

Un agente que vuelve a pedir el mismo rango de un archivo que aún tiene entero en la
conversación recibe una nota en vez de releerlo; si el archivo cambió, o el resultado
ya se recortó, se lee de nuevo.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.core.events import EventType
from satellite.orchestration.chat import ChatSession
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def _runtime(tmp_path: Path, monkeypatch, script: list[dict]):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 41)), encoding="utf-8")
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
    orchestrator, bus, _store, _registry = runtime_builder.build_runtime(project)
    events: list = []
    bus.subscribe(events.append)
    return project, orchestrator, events, provider


def _read(call_id: str, **arguments) -> dict:
    return {"tool_calls": [{"id": call_id, "name": "read_file", "arguments": arguments}]}


def _reads(events) -> int:
    return sum(
        1 for e in events if e.event_type is EventType.TOOL_CALLED and e.tool_name == "read_file"
    )


def test_the_same_range_is_not_read_twice(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _read("c1", path="app.py", start_line=1, end_line=10),
            _read("c2", path="app.py", start_line=1, end_line=10),
            {"content": "listo"},
        ],
    )

    run(ChatSession(project, orchestrator).send("¿qué hay en app.py?"))

    assert _reads(events) == 1
    note = json.loads(provider.requests[2].messages[-1].content)
    assert note["success"] is True
    assert note["output"]["already_read"] is True


def test_a_different_range_is_read(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, events, _provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _read("c1", path="app.py", start_line=1, end_line=10),
            _read("c2", path="app.py", start_line=11, end_line=20),
            {"content": "listo"},
        ],
    )

    run(ChatSession(project, orchestrator).send("¿qué hay en app.py?"))

    assert _reads(events) == 2


def test_once_the_result_was_truncated_it_is_read_again(tmp_path: Path, monkeypatch) -> None:
    from satellite.orchestration import pilot as pilot_module

    monkeypatch.setattr(pilot_module, "KEEP_TOOL_RESULTS", 1)
    project, orchestrator, events, _provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _read("c1", path="app.py", start_line=1, end_line=10),
            _read("c2", path="app.py", start_line=11, end_line=20),
            _read("c3", path="app.py", start_line=1, end_line=10),
            {"content": "listo"},
        ],
    )

    run(ChatSession(project, orchestrator).send("¿qué hay en app.py?"))

    # Con sólo 1 resultado entero, la primera lectura ya se recortó: se relee.
    assert _reads(events) == 3


def test_after_editing_the_file_it_is_read_again() -> None:
    from satellite.agents.runtime import _ReadLedger
    from satellite.core.tool_models import ToolCall

    ledger = _ReadLedger()
    read = ToolCall(id="r", name="read_file", arguments={"path": "app.py"})
    ledger.remember(read, ordinal=0)
    assert ledger.repeated(read, total=1, keep=8) is True

    ledger.forget_writes(ToolCall(id="e", name="edit_file", arguments={"path": "./app.py"}))

    assert ledger.repeated(read, total=2, keep=8) is False


def test_a_delegation_invalidates_what_the_pilot_read(tmp_path: Path, monkeypatch) -> None:
    """El coder puede cambiar el archivo: tras delegar, el piloto vuelve a leer de verdad."""
    coder_output = {
        "summary": "s",
        "files_changed": ["app.py"],
        "verification": {"command": "pytest", "passed": True, "details": "ok"},
    }
    project, orchestrator, events, _provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _read("c1", path="app.py", start_line=1, end_line=10),
            {"tool_calls": [{"id": "d", "name": "delegate", "arguments": {
                "agent_id": "python_coder", "task": {"goal": "edit app.py"}}}]},
            {"content": json.dumps(coder_output)},
            _read("c3", path="app.py", start_line=1, end_line=10),
            {"content": "listo"},
        ],
    )

    run(ChatSession(project, orchestrator).send("arregla app.py"))

    pilot_reads = [
        e for e in events
        if e.event_type is EventType.TOOL_CALLED and e.tool_name == "read_file"
        and e.node_id == "pilot"
    ]
    assert len(pilot_reads) == 2


def test_an_absolute_write_path_invalidates_the_relative_read(tmp_path: Path) -> None:
    from satellite.agents.runtime import _ReadLedger
    from satellite.core.tool_models import ToolCall

    ledger = _ReadLedger(project_root=tmp_path)
    read = ToolCall(id="r", name="read_file", arguments={"path": "src/app.py"})
    ledger.remember(read, ordinal=0)
    ledger.forget_writes(
        ToolCall(id="w", name="write_file", arguments={"path": str(tmp_path / "src" / "app.py")})
    )

    assert ledger.repeated(read, total=2, keep=8) is False
