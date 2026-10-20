"""Enrutado determinista de peticiones de escritorio a ``desktop_operator``."""

import asyncio
from pathlib import Path

from satellite.agents.base import AgentContext
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import (
    GraphRunner,
    Planner,
    PlanningResult,
    RuntimeServices,
)
from satellite.orchestration.desktop_flow import DesktopFlow
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.tools.registry import DefaultToolRegistry


def run(coro):
    return asyncio.run(coro)


class RecordingPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        return PlanningResult(kind="needs_clarification", question="¿qué quieres?")


class StubRunner(GraphRunner):
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


class FakeAgent:
    def __init__(self, output: dict, status=AgentStatus.OK) -> None:
        self.output = output
        self.status = status
        self.tasks: list[dict] = []

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.tasks.append(task)
        return AgentResult(
            node_id="desktop",
            spec_id="desktop_operator",
            status=self.status,
            output=dict(self.output),
        )


class FakeFactory:
    def __init__(self, agent: FakeAgent | None = None, available: bool = True) -> None:
        self.agent = agent or FakeAgent({"summary": "ok", "completed": True})
        self.available = available

    def can_create(self, spec_id: str) -> bool:
        return self.available and spec_id == "desktop_operator"

    def create(self, spec_id: str) -> FakeAgent:
        return self.agent


class SpyDesktopFlow:
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, task) -> TaskResult | None:
        self.runs += 1
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.OK,
            summary="desktop",
            output={"mode": "desktop"},
        )


def _orchestrator(planner: Planner, runner: GraphRunner, flow) -> DefaultOrchestrator:
    orch = DefaultOrchestrator(
        planner, runner, RuntimeServices(registry=None, tools=DefaultToolRegistry())
    )
    orch.desktop_flow = flow
    return orch


def test_flow_maps_agent_output_to_task_result(tmp_path: Path) -> None:
    agent = FakeAgent({"summary": "Abrí Safari en Google Docs.", "completed": True})
    flow = DesktopFlow(factory=FakeFactory(agent), project_root=tmp_path)

    result = run(flow.run(Task(goal="abre Safari y entra a Google Docs", project_root=tmp_path)))

    assert result is not None
    assert result.status is TaskStatus.OK
    assert result.summary == "Abrí Safari en Google Docs."
    assert result.output["mode"] == "desktop"
    assert result.output["completed"] is True
    assert agent.tasks[0]["goal"] == "abre Safari y entra a Google Docs"


def test_flow_returns_none_when_agent_is_unavailable(tmp_path: Path) -> None:
    flow = DesktopFlow(
        factory=FakeFactory(available=False), project_root=tmp_path
    )
    assert run(flow.run(Task(goal="abre Safari", project_root=tmp_path))) is None


def test_desktop_request_routes_to_flow_not_planner(tmp_path: Path) -> None:
    planner = RecordingPlanner()
    flow = SpyDesktopFlow()
    orch = _orchestrator(planner, StubRunner(), flow)

    result = run(
        orch.execute(
            Task(goal="Puedes abrir safari y entrar a google docs", project_root=tmp_path)
        )
    )

    assert flow.runs == 1
    assert planner.calls == 0
    assert result.output["mode"] == "desktop"
    assert result.summary == "desktop"


def test_unrelated_task_does_not_route_to_desktop(tmp_path: Path) -> None:
    planner = RecordingPlanner()
    flow = SpyDesktopFlow()
    orch = _orchestrator(planner, StubRunner(), flow)

    result = run(orch.execute(Task(goal="arregla el bug del login", project_root=tmp_path)))

    assert flow.runs == 0
    assert planner.calls == 1
    assert result.output["mode"] == "needs_clarification"
