"""Tests de FileStore."""

import asyncio
import threading
import time
from pathlib import Path

from satellite.core.events import EventType, ExecutionEvent
from satellite.core.task import Task
from satellite.persistence.store import FileStore


def run(coro):
    return asyncio.run(coro)


def test_task_roundtrip(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    task = Task(goal="crear html", project_root=tmp_path)
    run(store.save_task(task))
    loaded = run(store.load_task(task.task_id))
    assert loaded.task_id == task.task_id
    assert loaded.goal == "crear html"


def test_state_roundtrip(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    run(store.save_state("t1", {"status": "ok", "n": 3}))
    state = run(store.load_state("t1"))
    assert state["status"] == "ok"
    assert run(store.load_state("no_existe")) is None


def test_append_and_load_events_with_corrupt_line(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    ev1 = ExecutionEvent(event_type=EventType.NODE_STARTED, task_id="t1", node_id="n1")
    ev2 = ExecutionEvent(event_type=EventType.NODE_FINISHED, task_id="t1", node_id="n1", payload={"status": "ok"})
    run(store.append_event("t1", ev1))
    run(store.append_event("t1", ev2))
    # corrupt line
    with open(store._events_path("t1"), "a", encoding="utf-8") as fh:
        fh.write("no es json\n")
    events = run(store.load_events("t1"))
    assert len(events) == 2
    assert events[0].event_type.value == "node_started"
    assert events[1].event_type.value == "node_finished"


def test_list_tasks(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    run(store.save_task(Task(goal="a", project_root=tmp_path)))
    run(store.save_task(Task(goal="b", project_root=tmp_path)))
    assert len(store.list_tasks()) == 2


def test_append_events_preserves_order_under_concurrent_calls(
    tmp_path: Path, monkeypatch
) -> None:
    """El worker único serializa las escrituras aunque lleguen en paralelo."""
    store = FileStore(tmp_path)
    original = store._append_event_sync

    def slow_first(task_id: str, event: ExecutionEvent) -> None:
        if event.node_id == "n1":
            time.sleep(0.1)
        original(task_id, event)

    monkeypatch.setattr(store, "_append_event_sync", slow_first)
    events = [
        ExecutionEvent(event_type=EventType.NODE_STARTED, task_id="t1", node_id=f"n{i}")
        for i in range(1, 4)
    ]

    async def scenario() -> None:
        await asyncio.gather(*(store.append_event("t1", event) for event in events))

    run(scenario())
    loaded = run(store.load_events("t1"))
    assert [event.node_id for event in loaded] == ["n1", "n2", "n3"]


def test_append_event_does_not_block_the_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    store = FileStore(tmp_path)
    started = threading.Event()
    release = threading.Event()
    events_path = store._events_path("t1")
    real_open = open

    def slow_open(file, mode="r", *args, **kwargs):
        if Path(file) == events_path and mode == "a":
            started.set()
            assert release.wait(2)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", slow_open)
    event = ExecutionEvent(event_type=EventType.TASK_STARTED, task_id="t1")

    async def scenario() -> None:
        task = asyncio.create_task(store.append_event("t1", event))
        deadline = time.monotonic() + 2
        while not started.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert started.is_set()
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, 2)

    run(scenario())
    assert events_path.exists()


def test_load_events_does_not_block_the_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    store = FileStore(tmp_path)
    event = ExecutionEvent(event_type=EventType.TASK_STARTED, task_id="t1")
    run(store.append_event("t1", event))
    started = threading.Event()
    release = threading.Event()
    events_path = store._events_path("t1")
    real_read_text = Path.read_text

    def slow_read_text(path, *args, **kwargs):
        if path == events_path:
            started.set()
            assert release.wait(2)
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", slow_read_text)

    async def scenario() -> None:
        task = asyncio.create_task(store.load_events("t1"))
        deadline = time.monotonic() + 2
        while not started.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert started.is_set()
        assert not task.done()
        release.set()
        loaded = await asyncio.wait_for(task, 2)
        assert len(loaded) == 1

    run(scenario())
