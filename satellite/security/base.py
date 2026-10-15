"""Interfaz de la política de seguridad (deny-by-default)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class SecurityPolicy(ABC):
    """Decide si una llamada de tool está permitida."""

    @abstractmethod
    def check_tool(
        self,
        tool_id: str,
        arguments: dict,
        allowed_tools: set[str] | None = None,
    ) -> bool:
        """Devuelve True si la tool es ejecutable con esos argumentos."""
        raise NotImplementedError
