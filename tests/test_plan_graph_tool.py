"""Fase 2 (docs/PLAN-grafo-piloto.md): el piloto emite un grafo con la tool `plan_graph`.

Un turno con partes independientes produce un grafo que corre en paralelo; un cambio
simple sigue siendo una delegación directa; el grafo queda en la traza del turno; un
grafo mal formado se le devuelve al modelo como error corregible.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.core.events import EventType
from satellite.orchestration.chat import ChatSession
from satellite.persistence.store import FileStore
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def _runtime(tmp_path: Path, monkeypatch, script: list[dict]):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    (project / "b.py").write_text("y = 2\n", encoding="utf-8")
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"name": "fake", "model": "m"}), encoding="utf-8")
    provider = FakeLLM(script=script, default_content="")
    # Salida válida para coder y auditor a la vez (sus requeridos son disjuntos; el
    # runtime recorta al schema de cada uno). Así los nodos paralelos no dependen del
    # orden del script del piloto.
    delegated = FakeLLM(default_content=json.dumps({
        "summary": "hecho", "files_changed": [],
        "verification": {"command": "pytest", "passed": True, "details": "3 passed"},
        "verdict": "pass", "issues": [], "tests_added": [], "tests_run": {"command": "pytest", "passed": True, "details": "ok"},
    }))
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)

    def role_provider(_root, role, default):
        if role == "context":
            return FakeLLM(default_content="r")
        if role == "orchestrator":
            return provider  # el piloto sigue el script
        return delegated  # coders y auditores: salida válida, sin carrera de script

    monkeypatch.setattr(runtime_builder, "_role_provider", role_provider)
    orchestrator, bus, _store, _registry = runtime_builder.build_runtime(project)
    events: list = []
    bus.subscribe(events.append)
    return project, orchestrator, events, provider


def _coder_reply(goal: str) -> dict:
    return {"content": json.dumps({"summary": f"hecho {goal}", "files_changed": [], "verification": {"command": "pytest", "passed": True}})}


def test_the_pilot_runs_a_graph_for_independent_parts(tmp_path: Path, monkeypatch) -> None:
    graph_call = {"tool_calls": [{"id": "g1", "name": "plan_graph", "arguments": {"nodes": [
        {"id": "a", "agent_id": "python_coder", "task": {"goal": "edita a.py"}},
        {"id": "b", "agent_id": "python_coder", "task": {"goal": "edita b.py"}},
    ]}}]}
    project, orchestrator, events, _provider = _runtime(
        tmp_path, monkeypatch, [graph_call, {"content": "Listo: a.py y b.py."}],
    )

    reply = run(ChatSession(project, orchestrator).send("arregla a.py y b.py, son independientes"))

    assert reply.status == "ok"
    kinds = [e.event_type for e in events]
    assert EventType.GRAPH_BUILT in kinds
    assert kinds.count(EventType.DELEGATION_STARTED) >= 2  # un DELEGATION_STARTED por nodo
    state = run(FileStore(project).load_state(reply.task_id))
    assert [n["id"] for n in state["trace"]["graph"]] == ["a", "b"]
    assert all(n["ok"] for n in state["trace"]["graph"])


def test_a_verification_gate_waits_and_reports(tmp_path: Path, monkeypatch) -> None:
    graph_call = {"tool_calls": [{"id": "g1", "name": "plan_graph", "arguments": {"nodes": [
        {"id": "a", "agent_id": "python_coder", "task": {"goal": "edita a.py"}},
        {"id": "b", "agent_id": "python_coder", "task": {"goal": "edita b.py"}},
        {"id": "v", "agent_id": "python_auditor", "task": {"goal": "revisa a.py y b.py"},
         "depends_on": ["a", "b"], "run_if": "all_ok"},
    ]}}]}
    project, orchestrator, _events, _provider = _runtime(
        tmp_path, monkeypatch, [graph_call, {"content": "Revisado."}],
    )

    reply = run(ChatSession(project, orchestrator).send("arregla a y b y audítalos"))

    assert reply.status == "ok"
    state = run(FileStore(project).load_state(reply.task_id))
    ids = [n["id"] for n in state["trace"]["graph"]]
    assert ids == ["a", "b", "v"] and all(n["ok"] for n in state["trace"]["graph"])


def test_a_simple_change_still_uses_a_direct_delegation(tmp_path: Path, monkeypatch) -> None:
    delegate_call = {"tool_calls": [{"id": "d1", "name": "delegate", "arguments": {
        "agent_id": "python_coder", "task": {"goal": "edita a.py"}}}]}
    project, orchestrator, _events, _provider = _runtime(
        tmp_path, monkeypatch, [delegate_call, {"content": "Listo."}],
    )

    reply = run(ChatSession(project, orchestrator).send("arregla a.py"))

    assert reply.status == "ok"
    state = run(FileStore(project).load_state(reply.task_id))
    assert "graph" not in state["trace"]  # sin grafo: delegación directa
    assert len(state["trace"]["delegations"]) == 1


def test_an_invalid_graph_comes_back_to_the_model(tmp_path: Path, monkeypatch) -> None:
    bad_graph = {"tool_calls": [{"id": "g1", "name": "plan_graph", "arguments": {"nodes": [
        {"id": "a", "agent_id": "python_coder", "depends_on": ["ghost"]},
    ]}}]}
    project, orchestrator, _events, provider = _runtime(
        tmp_path, monkeypatch, [bad_graph, {"content": "Corregido."}],
    )

    reply = run(ChatSession(project, orchestrator).send("haz varias cosas"))

    assert reply.status == "ok"
    tool_msg = provider.requests[1].messages[-1].content
    assert "grafo inválido" in tool_msg or "invalid_graph" in tool_msg
