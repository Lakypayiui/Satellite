"""Singleton run-state registry for the web UI backend.

Tracks live execution runs and the events emitted during each run so that
polling and WebSocket clients can observe progress. Runs execute in a
threadpool (``asyncio.to_thread``) and push events through
``RunState.on_event``; the web layer drains them per run.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RunState:
    thread: Any = None
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"
    result: dict[str, Any] | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    done: Any = None  # threading.Event
    on_event: Callable[[dict[str, Any]], None] | None = None

    def emit(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(event)
        cb = self.on_event
        if cb is not None:
            try:
                cb(event)
            except Exception:
                pass
