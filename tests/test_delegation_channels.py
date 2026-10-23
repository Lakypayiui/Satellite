"""Fase B del plan de correcciones (docs/PLAN-correcciones.md).

2. Las aprobaciones de una delegación van por el puerto del turno, no por el global.
2b. MCP usa su propio puerto de aprobación (nunca la consola: stdin es el protocolo).
4. Los delegados reciben las instrucciones del proyecto (AGENTS.md).
6. El presupuesto de tokens de delegación descuenta lo que gastan los delegados.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentFactory
from satellite.agents.registry import DefaultAgentRegistry
from satellite.approval.base import ApprovalPort, AutoApprovalPort, DenyAllApprovalPort
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.core.result import AgentResult, AgentStatus, ExecutionMetrics
from satellite.core.spec import AgentSpec
from satellite.orchestration.delegation import DelegationManager


def run(coro):
    return asyncio.run(coro)


class _RecordingPort(ApprovalPort):
    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.requests: list[dict] = []

    async def request(self, proposal, ctx=None):
        self.requests.append(dict(proposal))
        status = ApprovalStatus.APPROVED if self.approve else ApprovalStatus.REJECTED
        return ApprovalDecision(status=status)


class _Agent(Agent):
    def __init__(self, tokens: int = 0) -> None:
        self.tokens = tokens
        self.contexts: list = []

    async def execute(self, task, ctx):
        self.contexts.append(ctx)
        return AgentResult(
            node_id="n",
            spec_id="child",
            status=AgentStatus.OK,
            summary="ok",
            metrics=ExecutionMetrics(tokens_in=self.tokens, tokens_out=0),
        )


class _Factory(AgentFactory):
    def __init__(self, agent: _Agent, context_engine=None) -> None:
        self.agent = agent
        self.context_engine = context_engine

    def create(self, spec_id, *, model_role=None):
        return self.agent


def _spec(agent_id: str, tools=()) -> AgentSpec:
    return AgentSpec(
        id=agent_id, name=agent_id, capabilities=("c",), prompt_ref=agent_id, tools=tuple(tools)
    )


def _registry(*specs) -> DefaultAgentRegistry:
    registry = DefaultAgentRegistry()
    for item in specs:
        registry.register_spec(item)
    return registry


# --- 2 · canal de aprobación ---------------------------------------------------------


def test_escalation_is_asked_on_the_turn_port_not_the_global_one(tmp_path: Path) -> None:
    global_port = _RecordingPort()
    turn_port = _RecordingPort()
    caller = _spec("mi_agente", tools=("read_file",))
    callee = _spec("ayudante", tools=("read_file", "write_file"))  # más tools: escalado
    manager = DelegationManager(
        _Factory(_Agent()),
        registry=_registry(caller, callee),
        approval=global_port,
        require_can_call=False,
    )

    result = run(
        manager.delegate(
            agent_id="ayudante",
            task={},
            parent_ctx=manager.root_context("t"),
            project_root=tmp_path,
            caller_spec=caller,
            approval=turn_port,
        )
    )

    assert result.ok is True
    assert [r["kind"] for r in turn_port.requests] == ["permission_escalation"]
    assert global_port.requests == []


def test_without_a_turn_port_the_global_one_is_used(tmp_path: Path) -> None:
    global_port = _RecordingPort(approve=False)
    caller = _spec("mi_agente", tools=("read_file",))
    callee = _spec("ayudante", tools=("read_file", "write_file"))
    manager = DelegationManager(
        _Factory(_Agent()),
        registry=_registry(caller, callee),
        approval=global_port,
        require_can_call=False,
    )

    result = run(
        manager.delegate(
            agent_id="ayudante",
            task={},
            parent_ctx=manager.root_context("t"),
            project_root=tmp_path,
            caller_spec=caller,
        )
    )

    assert result.ok is False
    assert len(global_port.requests) == 1


def test_mcp_runtime_uses_the_mcp_approval_port(tmp_path: Path, monkeypatch) -> None:
    from satellite.mcp import handlers

    captured: dict = {}

    def fake_build(root, *, allow_desktop=True, approval=None):
        captured["approval"] = approval
        return object(), object(), object(), object()

    monkeypatch.setattr(handlers, "build_runtime", fake_build)
    bundle = handlers.RuntimeBundle()
    bundle.get(tmp_path)

    assert captured["approval"] is bundle.approval


# --- 4 · AGENTS.md en los delegados ---------------------------------------------------


def test_a_delegate_receives_the_project_instructions(tmp_path: Path) -> None:
    from satellite.context.engine import DefaultContextEngine

    (tmp_path / "AGENTS.md").write_text("Usa tabs, nunca espacios.\n", encoding="utf-8")
    agent = _Agent()
    manager = DelegationManager(
        _Factory(agent, context_engine=DefaultContextEngine(tmp_path)),
        registry=_registry(_spec("python_coder")),
        approval=AutoApprovalPort(),
    )

    run(
        manager.delegate(
            agent_id="python_coder",
            task={},
            parent_ctx=manager.root_context("t"),
            project_root=tmp_path,
            caller_spec=_spec("pilot"),
        )
    )

    sections = agent.contexts[0].context.sections
    assert any("Usa tabs, nunca espacios." in section.content for section in sections)


def test_without_instructions_the_delegate_context_stays_empty(tmp_path: Path) -> None:
    from satellite.context.engine import DefaultContextEngine

    agent = _Agent()
    manager = DelegationManager(
        _Factory(agent, context_engine=DefaultContextEngine(tmp_path)),
        registry=_registry(_spec("python_coder")),
        approval=AutoApprovalPort(),
    )

    run(
        manager.delegate(
            agent_id="python_coder",
            task={},
            parent_ctx=manager.root_context("t"),
            project_root=tmp_path,
            caller_spec=_spec("pilot"),
        )
    )

    assert agent.contexts[0].context.sections == ()


# --- 6 · presupuesto de tokens --------------------------------------------------------


def test_the_token_budget_counts_what_delegates_spend(tmp_path: Path) -> None:
    manager = DelegationManager(
        _Factory(_Agent(tokens=100)),
        registry=_registry(_spec("python_coder")),
        token_budget=150,
        approval=DenyAllApprovalPort(),
    )
    root = manager.root_context("t")

    async def twice():
        first = await manager.delegate(
            agent_id="python_coder", task={}, parent_ctx=root,
            project_root=tmp_path, caller_spec=_spec("pilot"),
        )
        second = await manager.delegate(
            agent_id="python_coder", task={}, parent_ctx=root,
            project_root=tmp_path, caller_spec=_spec("pilot"),
        )
        third = await manager.delegate(
            agent_id="python_coder", task={}, parent_ctx=root,
            project_root=tmp_path, caller_spec=_spec("pilot"),
        )
        return first, second, third

    first, second, third = run(twice())

    assert first.ok and second.ok  # 100 < 150 al empezar la segunda
    assert third.ok is False
    assert "token budget" in (third.error or "")
    assert root.remaining_tokens() == 0


def test_a_nested_delegate_shares_the_turn_budget(tmp_path: Path) -> None:
    manager = DelegationManager(_Factory(_Agent()), token_budget=100)
    root = manager.root_context("t")
    child = root.child("python_coder")
    child.spend(60)
    assert root.remaining_tokens() == 40
    assert child.remaining_tokens() == 40


def test_the_budget_is_read_from_the_config(tmp_path: Path, monkeypatch) -> None:
    import json

    from satellite.cli import runtime_builder
    from satellite.providers.fake import FakeLLM

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m", "llm": {"delegation": {"token_budget": 5000}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: FakeLLM())
    orchestrator, _bus, _store, _registry = runtime_builder.build_runtime(tmp_path)

    assert orchestrator.services.delegation.token_budget == 5000
