"""Dos ``satellite_run`` a la vez sobre el mismo proyecto comparten runtime (y su token
de cancelación): el segundo se rechaza como ocupado en vez de pisar al primero."""

from __future__ import annotations

import asyncio

from satellite.core.task import TaskResult, TaskStatus
from satellite.mcp.handlers import get_handlers


class _SlowOrchestrator:
    def __init__(self) -> None:
        self.running = 0
        self.peak = 0

    async def execute(self, task):
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0.1)
        self.running -= 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")

    def cancel(self, task_id: str = "", reason: str = "") -> dict:
        return {"cancelled": True, "task_id": task_id, "reason": reason}


class _Bundle:
    def __init__(self) -> None:
        self._active: dict = {}
        self.orchestrator = _SlowOrchestrator()

    def get(self, root):
        return {"orchestrator": self.orchestrator, "registry": None, "store": None}


def test_a_second_run_on_the_same_project_is_refused_while_the_first_runs(tmp_path) -> None:
    bundle = _Bundle()
    handlers = get_handlers(bundle)  # type: ignore[arg-type]

    async def scenario():
        first = asyncio.ensure_future(
            handlers["satellite_run"]({"goal": "a", "project_root": str(tmp_path)})
        )
        await asyncio.sleep(0.02)
        second = await handlers["satellite_run"]({"goal": "b", "project_root": str(tmp_path)})
        return await first, second

    first, second = asyncio.run(scenario())

    assert "status: ok" in first["text"]
    assert second["isError"] is True and "en curso" in second["text"]
    assert bundle.orchestrator.peak == 1


def test_runs_on_different_projects_may_overlap(tmp_path) -> None:
    bundle = _Bundle()
    handlers = get_handlers(bundle)  # type: ignore[arg-type]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    async def scenario():
        return await asyncio.gather(
            handlers["satellite_run"]({"goal": "a", "project_root": str(tmp_path / "a")}),
            handlers["satellite_run"]({"goal": "b", "project_root": str(tmp_path / "b")}),
        )

    results = asyncio.run(scenario())

    assert all("status: ok" in r["text"] for r in results)
