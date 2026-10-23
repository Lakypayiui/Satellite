"""Segunda revisión del flujo piloto (docs/PLAN-correcciones.md, revisión 2026-10-22).

Hallazgos del núcleo de orquestación: turno cancelado legible, cancelación durante la
preparación, destinos no delegables, ventana de compactación, resultados de delegación
enteros, rutas relativas al proyecto y cierre del turno aunque falle la persistencia.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.agents.base import Agent, AgentFactory
from satellite.agents.registry import DefaultAgentRegistry
from satellite.approval.base import DenyAllApprovalPort
from satellite.cli import runtime_builder
from satellite.core.events import EventType
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.spec import AgentSpec
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.delegation import DelegationManager
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


# --- 1 · un turno cancelado se lee, no se vuelca como JSON -----------------------------


def test_a_cancelled_turn_has_readable_text(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, _events, _provider = _runtime(tmp_path, monkeypatch, [])
    factory = orchestrator.services.factory
    real_create = factory.create

    def create(spec_id, **kwargs):
        agent = real_create(spec_id, **kwargs)

        async def cancelled(task, ctx):
            return AgentResult(
                node_id="pilot", spec_id="pilot", status=AgentStatus.CANCELLED,
                summary="Nodo cancelado: cancelado desde la TUI",
                output={"cancelled": True, "reason": "cancelado desde la TUI"},
            )

        agent.execute = cancelled
        return agent

    monkeypatch.setattr(factory, "create", create)

    reply = run(ChatSession(project, orchestrator).send("haz algo largo"))

    assert reply.status == "cancelled"
    assert "{" not in reply.text
    assert "cancel" in reply.text.lower()


# --- 5 · cancelar mientras se prepara el turno ------------------------------------------


def test_a_cancel_requested_before_the_turn_starts_is_honored(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, _events, provider = _runtime(
        tmp_path, monkeypatch, [{"content": "no debería llegar"}]
    )
    real_save = orchestrator.store.save_task

    async def save_and_cancel(task):
        # prepare_turn está en marcha (indexando, guardando): el usuario pulsa cancelar.
        orchestrator.services.cancel.request("cancelado mientras se preparaba")
        return await real_save(task)

    monkeypatch.setattr(orchestrator.store, "save_task", save_and_cancel)

    reply = run(ChatSession(project, orchestrator).send("hola"))

    assert reply.status == "cancelled"
    assert provider.requests == []


# --- 4 · ni el piloto ni el creador son destinos de delegación --------------------------


class _Agent(Agent):
    async def execute(self, task, ctx):
        return AgentResult(node_id="n", spec_id="x", status=AgentStatus.OK, summary="ok")


class _Factory(AgentFactory):
    def create(self, spec_id, *, model_role=None):
        return _Agent()


def _spec(agent_id: str) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, capabilities=("c",), prompt_ref=agent_id)


def test_the_pilot_cannot_delegate_to_the_creator_or_to_itself(tmp_path: Path) -> None:
    from satellite.events.bus import EventBus

    bus = EventBus()
    events: list = []
    bus.subscribe(events.append)
    registry = DefaultAgentRegistry()
    for spec in (_spec("pilot"), _spec("agent_creator"), _spec("python_coder")):
        registry.register_spec(spec)
    manager = DelegationManager(
        _Factory(), registry=registry, sink=bus, approval=DenyAllApprovalPort()
    )

    async def scenario():
        root = manager.root_context("t")
        creator = await manager.delegate(
            agent_id="agent_creator", task={}, parent_ctx=root,
            project_root=tmp_path, caller_spec=registry.get("pilot"),
        )
        pilot = await manager.delegate(
            agent_id="pilot", task={}, parent_ctx=root,
            project_root=tmp_path, caller_spec=registry.get("pilot"),
        )
        coder = await manager.delegate(
            agent_id="python_coder", task={}, parent_ctx=root,
            project_root=tmp_path, caller_spec=registry.get("pilot"),
        )
        return creator, pilot, coder

    creator, pilot, coder = run(scenario())

    assert creator.ok is False and "create_agent" in (creator.error or "")
    assert pilot.ok is False
    assert coder.ok is True
    started = [e.payload["agent_id"] for e in events if e.event_type is EventType.DELEGATION_STARTED]
    assert started == ["python_coder"]


# --- 3 · la ventana de compactación cuenta los resultados de esta ronda -------------------


def test_already_read_is_not_granted_when_the_original_is_about_to_be_truncated(
    tmp_path: Path, monkeypatch
) -> None:
    from satellite.orchestration import pilot as pilot_module

    monkeypatch.setattr(pilot_module, "KEEP_TOOL_RESULTS", 2)
    project, orchestrator, events, provider = _runtime(
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

    reads = [
        e for e in events if e.event_type is EventType.TOOL_CALLED and e.tool_name == "read_file"
    ]
    # Con 2 resultados enteros, al añadir el tercero el primero se recorta: se relee.
    assert len(reads) == 3
    last_request = provider.requests[-1].messages
    tool_messages = [m.content for m in last_request if m.role.value == "tool"]
    assert not any("already_read" in content for content in tool_messages)


# --- 6 · los resultados de delegación no se recortan -------------------------------------


def test_compaction_keeps_delegation_results_whole() -> None:
    from satellite.agents.runtime import TRUNCATED_NOTE, _compact_tool_results
    from satellite.core.messages import Message, Role
    from satellite.core.tool_models import ToolCall

    long = "x" * 2000
    messages = [
        Message(role=Role.USER, content="task"),
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=(ToolCall(id="d1", name="delegate", arguments={"agent_id": "python_coder"}),),
        ),
        Message(role=Role.TOOL, content=long, tool_call_id="d1"),
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=(ToolCall(id="r1", name="read_file", arguments={"path": "a.py"}),),
        ),
        Message(role=Role.TOOL, content=long, tool_call_id="r1"),
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=(ToolCall(id="r2", name="read_file", arguments={"path": "b.py"}),),
        ),
        Message(role=Role.TOOL, content=long, tool_call_id="r2"),
    ]

    out = _compact_tool_results(messages, keep=1)

    by_id = {m.tool_call_id: m.content for m in out if m.role is Role.TOOL}
    assert by_id["d1"] == long
    assert by_id["r1"].endswith(TRUNCATED_NOTE)
    assert by_id["r2"] == long


# --- 2 · las rutas se normalizan contra el proyecto, no contra el cwd ---------------------


def test_read_paths_are_relative_to_the_project_not_the_cwd(tmp_path: Path, monkeypatch) -> None:
    from satellite.agents.runtime import _ReadLedger
    from satellite.core.tool_models import ToolCall

    (tmp_path / "pkg").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "pkg")
    ledger = _ReadLedger(project_root=tmp_path)
    read = ToolCall(id="r", name="read_file", arguments={"path": "src/app.py"})
    ledger.remember(read, ordinal=0)

    ledger.forget_writes(
        ToolCall(id="w", name="edit_file", arguments={"path": str(tmp_path / "src" / "app.py")})
    )

    assert ledger.repeated(read, total=2, keep=8) is False


# --- 8 · TASK_FINISHED aunque falle la persistencia ---------------------------------------


def test_task_finished_is_emitted_even_if_the_store_fails(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, events, _provider = _runtime(tmp_path, monkeypatch, [{"content": "hola"}])

    async def broken(*_args, **_kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(orchestrator.store, "save_state", broken)

    reply = run(ChatSession(project, orchestrator).send("hola"))

    kinds = [e.event_type for e in events]
    assert kinds[-1] is EventType.TASK_FINISHED
    assert reply.status in ("ok", "failed")
