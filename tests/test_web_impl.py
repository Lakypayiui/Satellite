"""Tests de la web (FastAPI TestClient) con runtime inyectado."""

from fastapi.testclient import TestClient

from satellite.core.task import TaskResult, TaskStatus
from satellite.events.bus import EventBus
from satellite.web.app import create_app


def make_runtime():
    """Build a fake (orchestrator, bus, store, registry) for the web app."""

    class FakeOrch:
        async def execute(self, task):
            return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")

    class FakeStore:
        def __init__(self):
            self._tasks = []

        def list_tasks(self):
            return self._tasks

    class FakeRegistry:
        def all(self):
            return []

    bus = EventBus()
    return FakeOrch(), bus, FakeStore(), FakeRegistry()


def test_system_and_agents(tmp_path) -> None:
    runtime = make_runtime()
    client = TestClient(create_app(tmp_path, runtime=runtime))
    r = client.get("/api/system")
    assert r.status_code == 200
    assert r.json()["initialized"] is False
    assert r.json()["agents"] == 0
    r2 = client.get("/api/agents")
    assert r2.status_code == 200


def test_run_and_events(tmp_path) -> None:
    runtime = make_runtime()
    client = TestClient(create_app(tmp_path, runtime=runtime))
    r = client.post("/api/run", json={"goal": "crear html"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert run_id
    ev = client.get(f"/api/run/{run_id}")
    assert ev.status_code == 200
    assert "events" in ev.json()
    tl = client.get("/api/tasks")
    assert tl.status_code == 200


def test_index_served(tmp_path) -> None:
    runtime = make_runtime()
    client = TestClient(create_app(tmp_path, runtime=runtime))
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()
