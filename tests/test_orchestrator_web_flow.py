"""The orchestrator routes explicit web research to web_researcher."""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import (
    GraphRunner,
    Planner,
    RuntimeServices,
)
from satellite.orchestration.orchestrator import DefaultOrchestrator


def run(coro):
    return asyncio.run(coro)


class FailingPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        raise AssertionError("the web route should not call the planner")


class FailingRunner(GraphRunner):
    async def run(self, graph, task, services) -> TaskResult:
        raise AssertionError("the web route should not run a graph")


class StubWebFlow:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task: Task) -> TaskResult:
        self.calls += 1
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.OK,
            summary="resultado web",
            output={
                "mode": "web_research",
                "content": "resultado web",
                "sources": [],
            },
        )


def test_web_research_is_routed_directly(tmp_path: Path) -> None:
    planner = FailingPlanner()
    flow = StubWebFlow()
    orchestrator = DefaultOrchestrator(
        planner,
        FailingRunner(),
        RuntimeServices(registry=DefaultAgentRegistry()),
        allow_chat_direct=True,
    )
    orchestrator.web_flow = flow

    task = Task(
        goal="Puedes investigar en internet si se ha intentado algo igual?",
        project_root=tmp_path,
    )
    result = run(orchestrator.execute(task))

    assert flow.calls == 1
    assert planner.calls == 0
    assert result.status is TaskStatus.OK
    assert result.output["mode"] == "web_research"
