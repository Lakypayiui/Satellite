"""Tests del DefaultOrchestrator."""

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentContext
from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.graph import Edge, ExecutionGraph, Node
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.events.base import EventSink
from satellite.orchestration.base import GraphRunner, Planner, PlanningResult, RuntimeServices
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.persistence.base import Store
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


class FakeGraphRunner(GraphRunner):
    """Runner fake: devuelve OK o FAILED según config."""

    def __init__(self, status: str = "ok", graph: ExecutionGraph | None = None):
        self.status = status
        self.graph = graph
        self.runs = 0

    async def run(self, graph, task, services):
        self.runs += 1
        if self.status == "failed":
            return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED, error="dead-end")
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


class FakePlanner(Planner):
    """Planner fake: devuelve un grafo mínimo."""

    def __init__(self, graph: ExecutionGraph | None = None):
        self.graph = graph
        self.plans = 0

    async def plan(self, task, catalog, context_hint=()):
        self.plans += 1
        if self.graph is None:
            g = ExecutionGraph(entry_node="n1")
            g.add_node(Node(id="n1", spec_id="html_writer", task={"goal": "x"}))
            self.graph = g
        return PlanningResult(kind="plan", graph=self.graph)


class FakeStore(Store):
    def __init__(self):
        self.tasks = {}
        self.graphs = {}
        self.states = {}

    async def save_task(self, task):
        self.tasks[task.task_id] = task

    async def load_task(self, task_id):
        return self.tasks[task_id]

    async def save_graph(self, task_id, graph):
        self.graphs[task_id] = graph

    async def load_graph(self, task_id):
        return self.graphs[task_id]

    async def append_event(self, task_id, event):
        pass

    async def load_events(self, task_id):
        return []

    async def save_state(self, task_id, state):
        self.states[task_id] = state

    async def load_state(self, task_id):
        return self.states.get(task_id)


class Sink(EventSink):
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def subscribe(self, handler):
        pass


def make_task(tmp_path: Path) -> Task:
    return Task(goal="crear y verificar html", project_root=tmp_path)


def make_catalog() -> DefaultAgentRegistry:
    reg = DefaultAgentRegistry()
    reg.register_spec(AgentSpec(
        id="html_writer", name="W", capabilities=("write_html",), prompt_ref="p",
    ))
    reg.register_spec(AgentSpec(
        id="html_reviewer", name="R", capabilities=("review_html",), prompt_ref="p",
    ))
    return reg


def make_services() -> RuntimeServices:
    return RuntimeServices(registry=make_catalog(), sink=None)


def test_orchestrator_ok(tmp_path: Path) -> None:
    planner = FakePlanner()
    runner = FakeGraphRunner(status="ok")
    orch = DefaultOrchestrator(planner, runner, make_services())
    result = run(orch.execute(make_task(tmp_path)))
    assert result.status.value == "ok"
    assert planner.plans == 1
    assert runner.runs == 1


def test_orchestrator_replans_on_failure(tmp_path: Path) -> None:
    """Runner falla → replan → 2º intento OK."""
    planner = FakePlanner()
    runner = FakeGraphRunner(status="failed")
    orch = DefaultOrchestrator(planner, runner, make_services())
    result = run(orch.execute(make_task(tmp_path)))
    assert planner.plans == 2
    assert runner.runs == 2
    assert result.status.value == "failed"


def test_orchestrator_second_failure_returns_failed(tmp_path: Path) -> None:
    planner = FakePlanner()
    runner = FakeGraphRunner(status="failed")
    orch = DefaultOrchestrator(planner, runner, make_services())
    result = run(orch.execute(make_task(tmp_path)))
    assert result.status.value == "failed"
    assert result.error is not None


def test_orchestrator_saves_to_store(tmp_path: Path) -> None:
    planner = FakePlanner()
    runner = FakeGraphRunner(status="ok")
    store = FakeStore()
    sink = Sink()
    orch = DefaultOrchestrator(planner, runner, make_services(), store=store, sink=sink)
    task = make_task(tmp_path)
    result = run(orch.execute(task))
    assert task.task_id in store.tasks
    assert task.task_id in store.graphs
    assert task.task_id in store.states
    types = [e.event_type.value for e in sink.events]
    assert types[0] == "task_started"
    assert types[-1] == "task_finished"
