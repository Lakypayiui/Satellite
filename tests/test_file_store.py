"""Tests de FileStore."""

import asyncio
from pathlib import Path

from satellite.core.events import EventType, ExecutionEvent
from satellite.core.graph import Edge, ExecutionGraph, Node
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


def test_graph_roundtrip(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="html_writer", task={"goal": "x"}))
    graph.add_node(Node(id="n2", spec_id="html_reviewer"))
    graph.add_edge(Edge(id="e1", source="n1", target="n2"))
    run(store.save_graph("t1", graph))
    loaded = run(store.load_graph("t1"))
    assert loaded.entry_node == "n1"
    assert len(loaded.nodes) == 2
    assert len(loaded.edges) == 1


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
