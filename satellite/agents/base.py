"""ABCs del runtime de agentes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from satellite.core.spec import AgentSpec
from satellite.core.result import AgentResult, Finding
from satellite.core.context_models import ContextBundle

if TYPE_CHECKING:
    from satellite.events.base import EventSink


@dataclass
class AgentContext:
    """Contexto que recibe un agente en UNA ejecución de nodo."""

    context: ContextBundle = field(default_factory=ContextBundle)
    feedback: tuple[Finding, ...] = ()
    sink: EventSink | None = None
    task_id: str = ""
    node_id: str = ""
    project_root: Path | None = None
    # Puerto de aprobación de la sesión: el runtime lo usa para pedir permiso cuando
    # una tool pide una ruta fuera del proyecto.
    approval: object | None = None


class Agent(ABC):
    """Runtime de UN nodo (stateless entre nodos)."""

    @abstractmethod
    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        """Ejecuta el trabajo concreto del nodo."""
        raise NotImplementedError


class AgentFactory(ABC):
    """Crea instancias de Agent por spec_id."""

    @abstractmethod
    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        """Crea un Agent para el spec indicado."""
        raise NotImplementedError


class AgentRegistry(ABC):
    """Catálogo de AgentSpec disponibles."""

    @abstractmethod
    def register_spec(self, spec: AgentSpec) -> None:
        """Registra un AgentSpec."""
        raise NotImplementedError

    @abstractmethod
    def get(self, spec_id: str) -> AgentSpec:
        """Devuelve el AgentSpec con ese id."""
        raise NotImplementedError

    @abstractmethod
    def find_by_capability(self, text: str) -> list[AgentSpec]:
        """Busca specs por capacidad (texto)."""
        raise NotImplementedError

    @abstractmethod
    def all(self) -> list[AgentSpec]:
        """Todos los specs registrados."""
        raise NotImplementedError


class PromptRegistry(ABC):
    """Registro de prompts por id (plantillas)."""

    @abstractmethod
    def get(self, prompt_id: str) -> str:
        """Devuelve el prompt con ese id."""
        raise NotImplementedError

    @abstractmethod
    def register(self, prompt_id: str, template: str) -> None:
        """Registra/actualiza un prompt."""
        raise NotImplementedError
