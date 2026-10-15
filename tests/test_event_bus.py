"""Tests de EventBus y EventBuffer."""

from satellite.core.events import EventType, ExecutionEvent
from satellite.events.bus import EventBuffer, EventBus


def make_event(task_id="t1", etype=EventType.NODE_STARTED, node_id="n1"):
    return ExecutionEvent(event_type=etype, task_id=task_id, node_id=node_id)


def test_bus_subscribe_receives() -> None:
    bus = EventBus()
    received = []
    bus.subscribe(received.append)
    bus.emit(make_event())
    assert len(received) == 1
    assert received[0].event_type.value == "node_started"


def test_bus_failing_listener_does_not_break() -> None:
    bus = EventBus()
    good = []
    bad_errors = []

    def bad(event):
        raise RuntimeError("boom")

    bus.subscribe(bad)
    bus.subscribe(good.append)
    bus.emit(make_event())  # no debe lanzar
    assert len(good) == 1


def test_buffer_events_since() -> None:
    buffer = EventBuffer()
    buffer.append(make_event())
    buffer.append(make_event(node_id="n2"))
    buffer.append(make_event(node_id="n3"))
    events, next_idx = buffer.events_since("t1", 1)
    assert len(events) == 2
    assert next_idx == 3
    # desde el final → vacío
    empty, idx = buffer.events_since("t1", 3)
    assert empty == []
    assert idx == 3


def test_buffer_reset_and_prune() -> None:
    buffer = EventBuffer()
    for i in range(5):
        buffer.append(make_event(node_id=f"n{i}"))
    buffer.prune(2)
    assert len(buffer.all("t1")) == 2
    buffer.reset("t1")
    assert buffer.all("t1") == []
