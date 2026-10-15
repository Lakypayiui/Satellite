"""Interfaz de almacenamiento (.satellite/)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from satellite.core.task import Task
from satellite.core.graph import ExecutionGraph
from satellite.core.events import ExecutionEvent


class Store(ABC):
    """Persistencia de estado de Satellite en .satellite/ (Etapa 7)."""

    @abstractmethod
    async def save_task(self, task: Task) -> None:
        """Guarda un Task."""
        raise NotImplementedError

    @abstractmethod
    async def load_task(self, task_id: str) -> Task:
        """Carga un Task por id."""
        raise NotImplementedError

    @abstractmethod
    async def save_graph(self, task_id: str, graph: ExecutionGraph) -> None:
        """Guarda el grafo de una tarea."""
        raise NotImplementedError

    @abstractmethod
    async def load_graph(self, task_id: str) -> ExecutionGraph:
        """Carga el grafo de una tarea."""
        raise NotImplementedError

    @abstractmethod
    async def append_event(self, task_id: str, event: ExecutionEvent) -> None:
        """Añade un evento al log de la tarea."""
        raise NotImplementedError

    @abstractmethod
    async def load_events(self, task_id: str) -> list[ExecutionEvent]:
        """Carga los eventos de una tarea."""
        raise NotImplementedError

    @abstractmethod
    async def save_state(self, task_id: str, state: dict) -> None:
        """Guarda estado arbitrario de la tarea."""
        raise NotImplementedError

    @abstractmethod
    async def load_state(self, task_id: str) -> dict | None:
        """Carga el estado de la tarea (None si no existe)."""
        raise NotImplementedError
