"""EventBus and EventBuffer: fan-out sink plus pollable buffer."""

from __future__ import annotations

from satellite.core.events import ExecutionEvent
from satellite.events.base import EventHandler, EventSink


class EventBus(EventSink):
    """Fan-out sink; a failing listener never breaks the bus."""

    def __init__(self) -> None:
        self._listeners: list[EventHandler] = []

    def emit(self, event: ExecutionEvent) -> None:
        for handler in list(self._listeners):
            try:
                handler(event)
            except Exception:
                continue

    def subscribe(self, handler: EventHandler) -> None:
        if handler not in self._listeners:
            self._listeners.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        if handler in self._listeners:
            self._listeners.remove(handler)


class EventBuffer:
    """Per-task buffer of ExecutionEvents with index-based polling."""

    def __init__(self) -> None:
        self._events: dict[str, list[ExecutionEvent]] = {}

    def append(self, event: ExecutionEvent) -> None:
        self._events.setdefault(event.task_id, []).append(event)

    def events_since(self, task_id: str, after_index: int) -> tuple[list[ExecutionEvent], int]:
        """Return (events newer than after_index, next_index)."""
        events = self._events.get(task_id, [])
        start = max(0, after_index)
        return events[start:], len(events)

    def reset(self, task_id: str) -> None:
        self._events.pop(task_id, None)

    def prune(self, older_than: int) -> None:
        """Keep at most this many events per task (drop oldest)."""
        for task_id in list(self._events):
            events = self._events[task_id]
            if len(events) > older_than:
                self._events[task_id] = events[-older_than:]

    def all(self, task_id: str) -> list[ExecutionEvent]:
        return list(self._events.get(task_id, []))
