"""Interfaz de la política de seguridad (deny-by-default)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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

    def check_path(self, path: Path, project_root: Path) -> bool:
        """True si la ruta es accesible bajo esta política.

        Por defecto **deniega**: una política que no implemente comprobaciones de
        filesystem no autoriza ninguna ruta. Así una implementación mínima falla
        cerrada en vez de provocar ``AttributeError`` en las tools.
        """
        return False

    def check_command(self, argv: list[str]) -> bool:
        """True si el comando es ejecutable bajo esta política.

        Por defecto **deniega**: una política que no implemente comprobaciones de
        terminal no autoriza ningún comando.
        """
        return False
