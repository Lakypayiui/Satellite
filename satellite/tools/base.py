"""ABCs de la tool-layer propia."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from satellite.core.tool_models import ToolCall, ToolResult, ToolSchema
from satellite.core.errors import ToolError

if TYPE_CHECKING:
    from satellite.events.base import EventSink
    from satellite.security.base import SecurityPolicy


@dataclass
class ToolContext:
    """Contexto de ejecución de una tool (paths, seguridad, sink)."""

    project_root: Path
    workdir: Path | None = None
    allowed_paths: tuple[Path, ...] | None = None
    env: dict = field(default_factory=dict)
    sink: EventSink | None = None
    policy: SecurityPolicy | None = None
    task_id: str = ""
    node_id: str = ""
    engine: object | None = None
    skills: object | None = None
    task_state: dict = field(default_factory=dict)
    delegation: object | None = None
    delegation_ctx: object | None = None
    active_spec: object | None = None
    # Puerto de aprobación (por sesión): el runtime lo usa para pedir permiso
    # cuando la política deniega una ruta autorizable.
    approval: object | None = None
    # Comandos concedidos por una aprobación puntual, solo para esta llamada.
    allowed_commands: tuple[str, ...] = ()


def granted_for_this_call(argv: list[str] | tuple[str, ...], tool_ctx) -> bool:
    """True si una aprobación puntual concedió ese binario para esta llamada.

    Espejo de ``fs._granted_for_this_call`` para comandos: la concesión «una vez» viaja
    en el contexto y no toca la política.
    """
    if not argv:
        return False
    binary = str(argv[0])
    return binary in tuple(getattr(tool_ctx, "allowed_commands", None) or ())


class Tool(ABC):
    """Una acción ejecutable por los agentes (filesystem/terminal/tests/git)."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Identificador único de la tool."""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """Descripción en lenguaje natural para el LLM."""
        raise NotImplementedError

    @property
    def categories(self) -> tuple[str, ...]:
        """Categorías de la tool."""
        return ()

    @property
    def parameters_schema(self) -> dict:
        """JSON Schema de los parámetros."""
        return {}

    @abstractmethod
    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        """Ejecuta la tool con los argumentos dados."""
        raise NotImplementedError

    def schema(self) -> ToolSchema:
        """ToolSchema derivada de la tool."""
        return ToolSchema(
            name=self.id,
            description=self.description,
            parameters=self.parameters_schema,
        )


class ToolRegistry(ABC):
    """Registro de tools disponibles."""

    @abstractmethod
    def register(self, tool: Tool) -> None:
        """Registra una tool."""
        raise NotImplementedError

    @abstractmethod
    def get(self, tool_id: str) -> Tool:
        """Devuelve la tool; ToolError si no existe."""
        raise NotImplementedError

    @abstractmethod
    def list_for(self, tool_ids: list[str]) -> list[Tool]:
        """Lista las tools con esos ids."""
        raise NotImplementedError

    @abstractmethod
    def schemas_for(self, tool_ids: list[str]) -> list[ToolSchema]:
        """Schemas de las tools con esos ids."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, call: ToolCall, tool_ctx: ToolContext) -> ToolResult:
        """Ejecuta la tool referida por el ToolCall."""
        raise NotImplementedError
