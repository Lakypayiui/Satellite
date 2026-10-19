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
    # Idioma del mensaje original de la persona, no del brief interno del planificador.
    # Vacío = desconocido: la instrucción de idioma pide detectarlo del mensaje.
    user_language: str = ""
    # Puerto de aprobación de la sesión: el runtime lo usa para pedir permiso cuando
    # una tool pide una ruta fuera del proyecto.
    approval: object | None = None
    # Token de cancelación de la ejecución en curso: el agente lo mira antes de gastar
    # una llamada al LLM o de lanzar una herramienta.
    cancel: object | None = None


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

    def can_create(self, spec_id: str) -> bool:
        """¿Se puede construir un Agent para ``spec_id``? Predicate de *preflight*.

        Quien sólo quiera saber si el agente está disponible debe llamar aquí, no a
        ``create``: construir una instancia puede tener coste o efectos que un sondeo
        no debería provocar. Por defecto se sondea ``create``; las fábricas con una
        comprobación más barata (sin instanciar) deben sobrescribirlo.
        """
        try:
            self.create(spec_id)
            return True
        except Exception:  # noqa: BLE001 — cualquier fallo al construir = no disponible
            return False


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
