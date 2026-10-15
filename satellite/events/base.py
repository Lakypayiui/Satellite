"""Interfaz de consumo de eventos (polling primero; SSE/WebSocket después)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from satellite.core.events import ExecutionEvent

EventHandler = Callable[[ExecutionEvent], None]


class EventSink(ABC):
    """Suscripción/emisión de ExecutionEvents."""

    @abstractmethod
    def emit(self, event: ExecutionEvent) -> None:
        """Emite un evento a los suscriptores."""
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, handler: EventHandler) -> None:
        """Registra un handler para eventos entrantes."""
        raise NotImplementedError
