"""Fase 1 del grafo de ejecución (docs/PLAN-grafo-piloto.md): el ejecutor por olas.

Ramas independientes en paralelo; los dependientes esperan a sus fuentes; la puerta
``run_if`` omite un nodo cuyas dependencias fallaron; reintentos por nodo; cancelar a
mitad deja lo hecho; el resumen de una dependencia llega al nodo que la espera.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentFactory
from satellite.agents.registry import DefaultAgentRegistry
from satellite.approval.base import AutoApprovalPort
from satellite.core.result import AgentResult, AgentStatus, ExecutionMetrics
from satellite.core.spec import AgentSpec
from satellite.orchestration.delegation import DelegationManager
from satellite.orchestration.graph import ExecutionGraph, GraphNode
from satellite.orchestration.graph_executor import GraphExecutor


def run(coro):
    return asyncio.run(coro)


class _Agent(Agent):
    """Agente de prueba: su comportamiento depende del goal de la tarea."""

    def __init__(self, hook=None) -> None:
        self.hook = hook
        self.seen: list[dict] = []

    async def execute(self, task, ctx):
        self.seen.append(task)
        if self.hook is not None:
            await self.hook(task)
        goal = str(task.get("goal") or "")
        status = AgentStatus.FAILED if "FALLA" in goal else AgentStatus.OK
        return AgentResult(
            node_id="n", spec_id="x", status=status,
            summary=f"hecho {goal}",
            output={"summary": f"hecho {goal}"},
            metrics=ExecutionMetrics(tokens_in=10, tokens_out=5),
        )


class _Factory(AgentFactory):
    def __init__(self, agent: _Agent) -> None:
        self.agent = agent

    def create(self, spec_id, *, model_role=None):
        return self.agent


def _manager(agent: _Agent, **kw) -> DelegationManager:
    registry = DefaultAgentRegistry()
    for spec_id in ("python_coder", "python_auditor", "pilot"):
        registry.register_spec(
            AgentSpec(id=spec_id, name=spec_id, capabilities=("c",), prompt_ref=spec_id)
        )
    return DelegationManager(_Factory(agent), registry=registry, approval=AutoApprovalPort(), **kw)


def _node(nid, *deps, agent_id="python_coder", goal="", **kw) -> GraphNode:
    return GraphNode(id=nid, agent_id=agent_id, task={"goal": goal or nid},
                     depends_on=tuple(deps), **kw)


def _root(manager):
    return manager.root_context("t")


def test_independent_branches_run_in_parallel() -> None:
    running = {"now": 0, "peak": 0}

    async def hook(_task):
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.05)
        running["now"] -= 1

    agent = _Agent(hook)
    manager = _manager(agent)
    graph = ExecutionGraph(nodes=(_node("a"), _node("b"), _node("c")))

    result = run(
        GraphExecutor(manager).run(graph, parent_ctx=_root(manager), project_root=Path("."), caller_spec=None)
    )

    assert result.ok is True
    assert running["peak"] == 3  # los tres corrieron a la vez


def test_a_dependent_waits_for_its_sources() -> None:
    order: list[str] = []

    async def hook(task):
        order.append(str(task.get("goal")))

    manager = _manager(_Agent(hook))
    graph = ExecutionGraph(nodes=(_node("a"), _node("b"), _node("verify", "a", "b", agent_id="python_auditor")))

    result = run(
        GraphExecutor(manager).run(graph, parent_ctx=_root(manager), project_root=Path("."), caller_spec=None)
    )

    assert result.ok is True
    assert order[-1] == "verify"  # verify corre después de a y b
    assert set(order[:2]) == {"a", "b"}


def test_a_gate_skips_a_node_when_a_dependency_fails() -> None:
    manager = _manager(_Agent())
    graph = ExecutionGraph(
        nodes=(_node("a", goal="FALLA"), _node("verify", "a", agent_id="python_auditor")),
    )

    result = run(
        GraphExecutor(manager).run(graph, parent_ctx=_root(manager), project_root=Path("."), caller_spec=None)
    )

    assert result.outcomes["a"].ok is False
    assert result.outcomes["verify"].skipped is True
    assert result.ok is False


def test_any_done_runs_even_if_a_dependency_failed() -> None:
    manager = _manager(_Agent())
    graph = ExecutionGraph(
        nodes=(_node("a", goal="FALLA"), _node("report", "a", agent_id="python_auditor", run_if="any_done")),
    )

    result = run(
        GraphExecutor(manager).run(graph, parent_ctx=_root(manager), project_root=Path("."), caller_spec=None)
    )

    assert result.outcomes["report"].skipped is False
    assert result.outcomes["report"].ok is True


def test_a_failed_node_is_retried_up_to_its_limit() -> None:
    calls = {"n": 0}

    class Flaky(_Agent):
        async def execute(self, task, ctx):
            calls["n"] += 1
            status = AgentStatus.OK if calls["n"] >= 3 else AgentStatus.FAILED
            return AgentResult(node_id="n", spec_id="x", status=status, summary="s",
                               output={}, metrics=ExecutionMetrics(tokens_in=1, tokens_out=1))

    manager = _manager(Flaky())
    graph = ExecutionGraph(nodes=(_node("a", max_retries=3),))

    result = run(
        GraphExecutor(manager).run(graph, parent_ctx=_root(manager), project_root=Path("."), caller_spec=None)
    )

    assert calls["n"] == 3
    assert result.outcomes["a"].ok is True
    assert result.outcomes["a"].attempts == 3


def test_the_upstream_summary_reaches_the_dependent_node() -> None:
    manager = _manager(_Agent())
    graph = ExecutionGraph(nodes=(_node("a", goal="edita a.py"), _node("verify", "a", agent_id="python_auditor")))

    run(GraphExecutor(manager).run(graph, parent_ctx=_root(manager), project_root=Path("."), caller_spec=None))

    verify_task = next(t for t in manager.factory.agent.seen if "upstream" in (t.get("context") or {}))
    [up] = verify_task["context"]["upstream"]
    assert up["node"] == "a" and "edita a.py" in up["summary"]


def test_cancelling_before_a_wave_stops_the_graph() -> None:
    class Cancel:
        def __init__(self) -> None:
            self.cancelled = False

    cancel = Cancel()

    async def hook(_task):
        cancel.cancelled = True  # tras la primera ola, pedir cancelar

    manager = _manager(_Agent(hook))
    graph = ExecutionGraph(nodes=(_node("a"), _node("b", "a")))

    result = run(
        GraphExecutor(manager).run(
            graph, parent_ctx=_root(manager), project_root=Path("."), caller_spec=None, cancel=cancel
        )
    )

    assert result.cancelled is True
    assert "a" in result.outcomes and "b" not in result.outcomes


def test_the_shared_token_budget_counts_graph_nodes() -> None:
    manager = _manager(_Agent(), token_budget=25)  # cada nodo gasta 15 (10+5)
    root = manager.root_context("t")
    graph = ExecutionGraph(nodes=(_node("a"), _node("b", "a"), _node("c", "b")))

    result = run(GraphExecutor(manager).run(graph, parent_ctx=root, project_root=Path("."), caller_spec=None))

    # a gasta 15; b arranca con 10 restantes (>0) y pasa; al llegar a c no queda nada.
    assert result.outcomes["a"].ok is True
    assert result.outcomes["b"].ok is True
    assert result.outcomes["c"].ok is False
    assert "token" in (result.outcomes["c"].error or "")
