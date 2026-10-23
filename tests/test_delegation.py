"""Tests de la delegación entre agentes (Fase 7)."""

import asyncio
from types import SimpleNamespace
from pathlib import Path

from satellite.agents.base import Agent, AgentFactory
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
    assert "depth" in reason


def test_context_cycle_detected() -> None:
    ctx = root_ctx(chain=("child",))
    ok, reason = ctx.can_delegate("child")
    assert ok is False
    assert "cycle" in reason


def test_context_token_budget() -> None:
    ctx = root_ctx(token_budget=10, tokens_used=10)
    ok, reason = ctx.can_delegate("child")
    assert ok is False
    assert "token" in reason


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
    assert "escalation" in (result.error or "")


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
    assert "depth" in (result.error or "")


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
    assert "cycle" in (result.error or "")


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


# --- Presupuesto de los delegados ---

class BudgetedAgent(FakeAgent):
    def __init__(self, spec_id: str, *, max_turns: int, budget=None, keep=None) -> None:
        super().__init__(spec_id)
        self.spec = SimpleNamespace(max_turns=max_turns)
        self.tool_round_budget = budget
        self.keep_tool_results = keep


def test_delegate_applies_tool_budget() -> None:
    from satellite.orchestration.delegation import (
        DELEGATE_KEEP_TOOL_RESULTS,
        DELEGATE_TOOL_ROUND_BUDGET,
        _apply_budget,
    )

    long = BudgetedAgent("coder", max_turns=40)
    _apply_budget(long)
    assert long.tool_round_budget == DELEGATE_TOOL_ROUND_BUDGET
    assert long.keep_tool_results == DELEGATE_KEEP_TOOL_RESULTS

    short = BudgetedAgent("quick", max_turns=5)
    _apply_budget(short)
    assert short.tool_round_budget == 5

    own = BudgetedAgent("own", max_turns=40, budget=3, keep=2)
    _apply_budget(own)
    assert (own.tool_round_budget, own.keep_tool_results) == (3, 2)

    plain = FakeAgent("plain")  # sin soporte de presupuesto: no se toca
    _apply_budget(plain)
    assert not hasattr(plain, "tool_round_budget")


def test_pilot_prompt_does_not_chain_auditor_after_coder() -> None:
    prompt = (
        Path(__file__).resolve().parent.parent
        / "satellite" / "agents" / "prompts" / "pilot.system.txt"
    ).read_text(encoding="utf-8")
    assert "auditor afterwards" in prompt


def test_pilot_prompt_hands_over_what_it_read() -> None:
    prompt = (
        Path(__file__).resolve().parent.parent
        / "satellite" / "agents" / "prompts" / "pilot.system.txt"
    ).read_text(encoding="utf-8")
    assert "exact places you read" in prompt
    assert "not the whole tests folder" in prompt


def test_delegation_reports_incomplete_work(tmp_path: Path) -> None:
    from satellite.core.result import ToolRecord

    class TiredAgent(FakeAgent):
        budget_exhausted = True

        async def execute(self, task, ctx):
            base = await super().execute(task, ctx)
            return AgentResult(
                node_id=base.node_id,
                spec_id=base.spec_id,
                status=AgentStatus.OK,
                summary="a medias",
                tool_calls=(
                    ToolRecord("edit_file", {"path": "a.py"}),
                    ToolRecord("edit_file", {"path": "a.py"}),
                    ToolRecord("write_file", {"path": "b.py"}, success=False),
                    ToolRecord("read_file", {"path": "c.py"}),
                    ToolRecord("read_file", {"path": "d.py", "start_line": 10, "end_line": 40}),
                    ToolRecord("read_file", {"path": "c.py"}),
                ),
            )

    class TiredFactory(FakeFactory):
        def create(self, spec_id, *, model_role=None):
            return TiredAgent(spec_id)

    registry = make_registry(spec("child"))
    manager = DelegationManager(TiredFactory(), registry=registry, approval=AutoApprovalPort())
    caller = spec("caller", can_call=(CanCallEntry("child", "delega"),))
    result = run(
        manager.delegate(
            agent_id="child", task={"goal": "x"}, parent_ctx=root_ctx(),
            project_root=tmp_path, caller_spec=caller,
        )
    )
    assert result.budget_exhausted is True
    assert result.files_touched == ("a.py",)
    assert result.files_read == ("c.py", "d.py:10-40")

    from satellite.tools.delegate import _progress

    progress = _progress(result)
    assert progress["incomplete"] is True
    assert progress["files_read"] == ["c.py", "d.py:10-40"]
    assert progress["files_touched"] == ["a.py"]
    assert "Do not repeat" in progress["note"]


def test_delegate_rejects_a_coder_of_another_language(tmp_path: Path) -> None:
    from satellite.tools.delegate import _language_mismatch

    python_task = {"goal": "add a test", "files": ["tests/test_policy.py", "README.md"]}
    error = _language_mismatch("javascript_coder", python_task)
    assert error is not None and "python_coder" in error
    assert _language_mismatch("python_coder", python_task) is None
    assert _language_mismatch("javascript_auditor", {"goal": "check src/App.tsx"}) is None
    assert _language_mismatch("python_coder", {"goal": "rename a variable"}) is None
    assert _language_mismatch("web_researcher", python_task) is None
    mixed = {"goal": "update api.py and App.tsx"}
    assert _language_mismatch("typescript_coder", mixed) is None
