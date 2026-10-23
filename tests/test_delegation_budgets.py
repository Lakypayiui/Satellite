"""Tests de presupuestos de delegación (profundidad y tokens) del DelegationManager."""

import asyncio

from satellite.agents.base import Agent, AgentFactory
from satellite.agents.registry import DefaultAgentRegistry
from satellite.approval.base import AutoApprovalPort
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.spec import AgentSpec, CanCallEntry
from satellite.orchestration.delegation import DelegationContext, DelegationManager


def run(coro):
    return asyncio.run(coro)


class FakeAgent(Agent):
    async def execute(self, task, ctx):
        return AgentResult(node_id=ctx.node_id or "n", spec_id="child", status=AgentStatus.OK, summary="ok")


class FakeFactory(AgentFactory):
    def create(self, spec_id, *, model_role=None):
        return FakeAgent()


def test_root_context_carries_budget() -> None:
    manager = DelegationManager(FakeFactory(), max_depth=4, token_budget=100)
    root = manager.root_context("t9")
    assert root.max_depth == 4
    assert root.token_budget == 100
    assert root.task_id == "t9"
    assert root.depth == 0


def test_child_context_accumulates_tokens() -> None:
    root = DelegationContext(task_id="t", max_depth=3, token_budget=100)
    child = root.child("a", tokens_used=30)
    grand = child.child("b", tokens_used=40)
    assert grand.tokens_used == 70
    assert grand.remaining_tokens() == 30
    assert grand.chain == ("a", "b")


def test_budget_exhausted_blocks_delegation() -> None:
    registry = DefaultAgentRegistry()
    registry.register_spec(AgentSpec(id="child", name="c", capabilities=("c",), prompt_ref="c"))
    manager = DelegationManager(FakeFactory(), registry=registry, max_depth=3, token_budget=50, approval=AutoApprovalPort())
    exhausted = DelegationContext(task_id="t", max_depth=3, token_budget=50, tokens_used=50)
    caller = AgentSpec(
        id="caller", name="x", capabilities=("c",), prompt_ref="c",
        can_call=(CanCallEntry("child"),),
    )
    from pathlib import Path

    result = run(
        manager.delegate(
            agent_id="child",
            task={},
            parent_ctx=exhausted,
            project_root=Path("."),
            caller_spec=caller,
        )
    )
    assert result.ok is False
    assert "token" in (result.error or "")

