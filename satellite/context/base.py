"""Interfaz del motor de contexto just-in-time."""

from __future__ import annotations

from abc import ABC, abstractmethod

from satellite.core.context_models import (
    ContextBundle,
    ContextLimits,
    ContextRef,
    ContextSnapshot,
)


class ContextEngine(ABC):
    """Resuelve referencias de contexto solo cuando se piden.

    Nunca construye contexto de lo que no se necesita; ``expand`` permite
    pedir más en caliente.
    """

    @abstractmethod
    async def resolve(
        self,
        refs: list[ContextRef] | tuple[ContextRef, ...],
        limits: ContextLimits | None = None,
    ) -> ContextBundle:
        """Resuelve las referencias pedidas dentro de los límites."""
        raise NotImplementedError

    @abstractmethod
    async def expand(
        self,
        node_id: str,
        new_refs: list[ContextRef] | tuple[ContextRef, ...],
    ) -> ContextBundle:
        """Pide contexto adicional en caliente para un nodo."""
        raise NotImplementedError

    @abstractmethod
    async def invalidate(self, paths: list[str]) -> None:
        """Invalida entradas de contexto cuyos paths cambiaron."""
        raise NotImplementedError

    @abstractmethod
    async def snapshot(self, node_id: str) -> ContextSnapshot:
        """Snapshot de auditoría: qué recibió el nodo."""
        raise NotImplementedError
