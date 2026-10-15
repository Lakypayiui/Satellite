"""Tests de la delegación entre agentes (Fase 7)."""

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.agents.registry import DefaultAgentRegistry
from satellite.approval.base import AutoApprovalPort, DenyAllApprovalPort
from satellite.core.result import AgentResult, AgentStatus, ExecutionMetrics
from satellite.core.spec import AgentSpec, CanCallEntry
from satellite.events.bus import EventBus
from satellite.orchestration.delegation import DelegationContext, DelegationManager
from satellite.tools.base import ToolContext
from satellite.tools.delegate import DelegateTool


def run(coro):
    return asyncio.run(coro)


class FakeAgent(Agent):
    def __init__(self, spec_id: str, summary: str = "ok") -> None:
        self.spec_id = spec_id
        self.summary = summary
        self.calls = 0

    async def execute(self, task, ctx):
        self.calls += 1
        return AgentResult(
            node_id=str(task.get("node_id", "n")),
            spec_id=self.spec_id,
            status=AgentStatus.OK,
            summary=self.summary,
            output={"summary": self.summary},
            metrics=ExecutionMetrics(tokens_in=1, tokens_out=1),
        )


class FakeFactory(AgentFactory):
    def __init__(self, summaries=None) -> None:
        self.summaries = summaries or {}
        self.created: list[str] = []

    def create(self, spec_id, *, model_role=None):
        self.created.append(spec_id)
        return FakeAgent(spec_id, self.summaries.get(spec_id, "ok"))


def spec(agent_id: str, *, tools=(), can_call=()) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        capabilities=("c",),
        prompt_ref=agent_id,
        tools=tuple(tools),
        can_call=tuple(can_call),
    )


def make_registry(*specs) -> DefaultAgentRegistry:
    reg = DefaultAgentRegistry()
    for item in specs:
        reg.register_spec(item)
    return reg


def root_ctx(**kwargs) -> DelegationContext:
    base = dict(task_id="t1", max_depth=3)
    base.update(kwargs)
    return DelegationContext(**base)


# --- DelegationContext ---

def test_context_depth_limit() -> None:
    ok, reason = root_ctx(depth=3, max_depth=3).can_delegate("child")
    assert ok is False
    assert "profundidad" in reason


def test_context_cycle_detected() -> None:
    ctx = root_ctx(chain=("child",))
    ok, reason = ctx.can_delegate("child")
    assert ok is False
    assert "ciclo" in reason


def test_context_token_budget() -> None:
    ctx = root_ctx(token_budget=10, tokens_used=10)
    ok, reason = ctx.can_delegate("child")
    assert ok is False
    assert "tokens" in reason


def test_context_allows_within_budget() -> None:
    ctx = root_ctx()
    ok, _ = ctx.can_delegate("child")
    assert ok is True
    child = ctx.child("child")
    assert child.depth == 1
    assert child.chain == ("child",)
    assert child.child("grand").chain == ("child", "grand")


# --- DelegationManager ---

def test_delegate_happy_path(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    factory = FakeFactory({"child": "hecho"})
    manager = DelegationManager(factory, registry=registry, approval=AutoApprovalPort())
    caller = spec("caller", can_call=(CanCallEntry("child", "delega"),))
    result = run(
        manager.delegate(
            agent_id="child",
            task={"goal": "x"},
            parent_ctx=root_ctx(),
            project_root=tmp_path,
            caller_spec=caller,
        )
    )
    assert result.ok is True
    assert result.summary == "hecho"
    assert factory.created == ["child"]


def test_delegate_requires_can_call(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    manager = DelegationManager(FakeFactory(), registry=registry, approval=DenyAllApprovalPort())
    caller = spec("caller")  # sin can_call
    result = run(
        manager.delegate(
            agent_id="child",
            task={},
            parent_ctx=root_ctx(),
            project_root=tmp_path,
            caller_spec=caller,
        )
    )
    assert result.ok is False
    assert "can_call" in (result.error or "")


def test_delegate_can_call_approved(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    manager = DelegationManager(FakeFactory(), registry=registry, approval=AutoApprovalPort())
    caller = spec("caller")  # sin can_call, pero se aprueba añadirlo
    result = run(
        manager.delegate(
            agent_id="child", task={}, parent_ctx=root_ctx(), project_root=tmp_path, caller_spec=caller
        )
    )
    assert result.ok is True


def test_delegate_escalation_denied(tmp_path: Path) -> None:
    registry = make_registry(spec("child", tools=("read_file", "write_file")))
    manager = DelegationManager(FakeFactory(), registry=registry, approval=DenyAllApprovalPort())
    caller = spec("caller", tools=("read_file",), can_call=(CanCallEntry("child"),))
    result = run(
        manager.delegate(
            agent_id="child", task={}, parent_ctx=root_ctx(), project_root=tmp_path, caller_spec=caller
        )
    )
    assert result.ok is False
    assert "escalado" in (result.error or "")


def test_delegate_escalation_approved(tmp_path: Path) -> None:
    registry = make_registry(spec("child", tools=("read_file", "write_file")))
    manager = DelegationManager(FakeFactory(), registry=registry, approval=AutoApprovalPort())
    caller = spec("caller", tools=("read_file",), can_call=(CanCallEntry("child"),))
    result = run(
        manager.delegate(
            agent_id="child", task={}, parent_ctx=root_ctx(), project_root=tmp_path, caller_spec=caller
        )
    )
    assert result.ok is True


def test_delegate_depth_limit_blocks(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    manager = DelegationManager(FakeFactory(), registry=registry, approval=AutoApprovalPort())
    result = run(
        manager.delegate(
            agent_id="child",
            task={},
            parent_ctx=root_ctx(depth=3, max_depth=3),
            project_root=tmp_path,
            caller_spec=spec("caller", can_call=(CanCallEntry("child"),)),
        )
    )
    assert result.ok is False
    assert "profundidad" in (result.error or "")


def test_delegate_cycle_blocks(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    manager = DelegationManager(FakeFactory(), registry=registry, approval=AutoApprovalPort())
    result = run(
        manager.delegate(
            agent_id="child",
            task={},
            parent_ctx=root_ctx(chain=("child",)),
            project_root=tmp_path,
            caller_spec=spec("caller", can_call=(CanCallEntry("child"),)),
        )
    )
    assert result.ok is False
    assert "ciclo" in (result.error or "")


def test_delegate_emits_events(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    bus = EventBus()
    seen: list = []
    bus.subscribe(seen.append)
    manager = DelegationManager(FakeFactory(), registry=registry, approval=AutoApprovalPort(), sink=bus)
    caller = spec("caller", can_call=(CanCallEntry("child"),))
    run(
        manager.delegate(
            agent_id="child", task={}, parent_ctx=root_ctx(), project_root=tmp_path, caller_spec=caller
        )
    )
    types = [event.event_type.value for event in seen]
    assert "delegation_started" in types
    assert "delegation_completed" in types


# --- DelegateTool ---

def test_delegate_tool_success(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    manager = DelegationManager(FakeFactory({"child": "hecho"}), registry=registry, approval=AutoApprovalPort())
    caller = spec("caller", can_call=(CanCallEntry("child", "delega"),))
    tool_ctx = ToolContext(
        project_root=tmp_path,
        delegation=manager,
        delegation_ctx=root_ctx(),
        active_spec=caller,
    )
    result = run(DelegateTool().execute({"agent_id": "child", "task": {"goal": "x"}}, tool_ctx))
    assert result.success is True
    assert result.output["summary"] == "hecho"


def test_delegate_tool_without_manager(tmp_path: Path) -> None:
    tool_ctx = ToolContext(project_root=tmp_path)
    result = run(DelegateTool().execute({"agent_id": "child"}, tool_ctx))
    assert result.success is False
    assert "no disponible" in (result.error or "")


def test_delegate_tool_requires_agent_id(tmp_path: Path) -> None:
    registry = make_registry(spec("child"))
    manager = DelegationManager(FakeFactory(), registry=registry, approval=AutoApprovalPort())
    tool_ctx = ToolContext(project_root=tmp_path, delegation=manager, delegation_ctx=root_ctx())
    result = run(DelegateTool().execute({}, tool_ctx))
    assert result.success is False
