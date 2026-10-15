"""Tests de DefaultGraphRunner: límites y terminación garantizada."""

import asyncio
from pathlib import Path

import pytest

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.core.errors import GraphValidationError
from satellite.core.graph import Edge, EdgeCondition, ExecutionGraph, GraphLimits, Node
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.runner import DefaultGraphRunner


def run(coro):
    return asyncio.run(coro)


class AlwaysFailAgent(Agent):
    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(node_id=ctx.node_id, spec_id="fail", status=AgentStatus.FAILED,
                           findings=())


class AlwaysFailFactory(AgentFactory):
    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        return AlwaysFailAgent()


def make_task(tmp_path: Path) -> Task:
    return Task(goal="x", project_root=tmp_path)


def make_fail_cycle(max_retries: int = 2, max_visits: int | None = None) -> ExecutionGraph:
    graph = ExecutionGraph(
        entry_node="a",
        limits=GraphLimits(
            max_node_visits=max_visits if max_visits is not None else 10,
        ),
    )
    graph.add_node(Node(id="a", spec_id="fail", max_retries=max_retries))
    graph.add_node(Node(id="b", spec_id="fail", max_retries=max_retries))
    graph.add_edge(Edge(id="e1", source="a", target="b", condition=EdgeCondition.on_status("failed"), payload="feedback"))
    graph.add_edge(Edge(id="e2", source="b", target="a", condition=EdgeCondition.on_status("failed"), payload="feedback"))
    return graph


def test_cycle_exhausted_retries(tmp_path: Path) -> None:
    """B falla siempre → tras max_retries → FAILED sin loop infinito."""
    graph = make_fail_cycle(max_retries=2)
    runner = DefaultGraphRunner(AlwaysFailFactory())
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=AlwaysFailFactory())))
    assert result.status.value == "failed"
    assert "reintentos" in (result.error or "")
    assert result.status.value == "failed"


def test_max_node_visits_aborta(tmp_path: Path) -> None:
    graph = make_fail_cycle(max_visits=3)
    runner = DefaultGraphRunner(AlwaysFailFactory())
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=AlwaysFailFactory())))
    assert result.status.value == "failed"
    assert "max_node_visits" in (result.error or "") or "reintentos" in (result.error or "")


def test_invalid_graph_raises_before_execution(tmp_path: Path) -> None:
    graph = ExecutionGraph(entry_node="inexistente")
    graph.add_node(Node(id="a", spec_id="fail"))
    runner = DefaultGraphRunner(AlwaysFailFactory())
    with pytest.raises(GraphValidationError):
        run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=AlwaysFailFactory())))


def test_ok_node_no_edges_is_fin_normal(tmp_path: Path) -> None:
    class OkAgent(Agent):
        async def execute(self, task, ctx):
            return AgentResult(node_id=ctx.node_id, spec_id="ok", status=AgentStatus.OK, summary="fin")

    class OkFactory(AgentFactory):
        def create(self, spec_id, *, model_role=None):
            return OkAgent()

    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="ok"))
    runner = DefaultGraphRunner(OkFactory())
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=OkFactory())))
    assert result.status.value == "ok"
    assert result.summary == "fin"
