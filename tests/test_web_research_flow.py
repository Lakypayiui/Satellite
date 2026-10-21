"""Deterministic route to web_researcher."""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task, TaskStatus
from satellite.orchestration.web_research_flow import WebResearchFlow


def run(coro):
    return asyncio.run(coro)


class FakeAgent:
    async def execute(self, task, ctx):
        return AgentResult(
            node_id=ctx.node_id,
            spec_id="web_researcher",
            status=AgentStatus.OK,
            summary="found evidence",
            output={
                "summary": "found evidence",
                "sources": [
                    {"title": "A paper", "url": "https://example.com/paper"},
                    "ignored",
                ],
            },
        )


class FakeFactory:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.created = 0

    def can_create(self, spec_id: str) -> bool:
        return not self.fail

    def create(self, spec_id: str):
        if self.fail:
            raise RuntimeError("no agent")
        self.created += 1
        return FakeAgent()


def _task(tmp_path: Path) -> Task:
    return Task(
        goal="Puedes investigar en internet si se ha intentado algo igual?",
        project_root=tmp_path,
    )


def test_flow_returns_summary_and_sources(tmp_path: Path) -> None:
    factory = FakeFactory()
    flow = WebResearchFlow(factory=factory, project_root=tmp_path)
    result = run(flow.run(_task(tmp_path)))
    assert result is not None
    assert result.status is TaskStatus.OK
    assert result.output["mode"] == "web_research"
    assert result.output["content"] == "found evidence"
    assert result.output["sources"] == [
        {"title": "A paper", "url": "https://example.com/paper"}
    ]
    assert factory.created == 1


def test_flow_falls_back_when_agent_is_unavailable(tmp_path: Path) -> None:
    flow = WebResearchFlow(factory=FakeFactory(fail=True), project_root=tmp_path)
    assert run(flow.run(_task(tmp_path))) is None
