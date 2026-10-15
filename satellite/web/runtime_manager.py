"""RuntimeManager: in-memory execution sessions keyed by task_id."""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.core.task import Task
from satellite.events.bus import EventBuffer


class RuntimeManager:
    """Tracks running tasks; exposes their events for polling."""

    def __init__(self, orchestrator, bus) -> None:
        self.orchestrator = orchestrator
        self.bus = bus
        self.buffer = EventBuffer()
        self.bus.subscribe(self.buffer.append)
        self._tasks: dict[str, dict] = {}

    def start(self, goal: str, project_root: Path) -> str:
        """Start a task in background; returns task_id."""
        task = Task(goal=goal, project_root=Path(project_root).resolve())
        self._tasks[task.task_id] = {"task": task, "status": "running", "result": None}
        asyncio.create_task(self._run(task))
        return task.task_id

    async def _run(self, task: Task) -> None:
        try:
            result = await self.orchestrator.execute(task)
            entry = self._tasks.get(task.task_id, {})
            entry["status"] = result.status.value
            entry["result"] = result
        except Exception as exc:  # noqa: BLE001
            entry = self._tasks.get(task.task_id, {})
            entry["status"] = "failed"
            entry["result"] = {"error": str(exc)}

    def status(self, task_id: str) -> dict:
        entry = self._tasks.get(task_id, {})
        task = entry.get("task")
        return {
            "task_id": task_id,
            "status": entry.get("status", "unknown"),
            "goal": task.goal if task is not None else "",
        }

    def result(self, task_id: str):
        """Devuelve el TaskResult (o dict de error) si ya terminó, sino None."""
        entry = self._tasks.get(task_id, {})
        return entry.get("result")

    def events_since(self, task_id: str, after_index: int) -> tuple[list, int]:
        return self.buffer.events_since(task_id, after_index)

    def graph_state(self, task_id: str) -> dict:
        """Derive per-node state from events."""
        events = self.buffer.all(task_id)
        nodes: dict[str, dict] = {}
        for ev in events:
            if ev.node_id is None:
                continue
            node = nodes.setdefault(ev.node_id, {"status": "pending", "retries": 0})
            if ev.event_type.value == "node_started":
                node["status"] = "running"
            elif ev.event_type.value == "node_finished":
                node["status"] = ev.payload.get("status", "ok")
            elif ev.event_type.value == "retry":
                node["retries"] += 1
            elif ev.event_type.value == "node_stuck":
                node["status"] = "stuck"
        return nodes
