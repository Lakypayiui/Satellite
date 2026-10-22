"""ABCs de la capa de coordinación global."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from satellite.core.task import Task, TaskResult
from satellite.core.graph import ExecutionGraph
from satellite.core.context_models import ContextRef
from satellite.core.plan import CapabilityGap, ExecutionPlan

if TYPE_CHECKING:
    from satellite.agents.base import AgentFactory, AgentRegistry
    from satellite.tools.base import ToolRegistry
    from satellite.context.base import ContextEngine
    from satellite.providers.base import LLMProvider
    from satellite.events.base import EventSink


@dataclass
class RuntimeServices:
    """Inyección de servicios al runner (parcial en Etapa 0)."""

    registry: AgentRegistry | None = None
    factory: AgentFactory | None = None
    tools: ToolRegistry | None = None
    context: ContextEngine | None = None
    provider: LLMProvider | None = None
    sink: EventSink | None = None
    # Puerto por defecto para ejecuciones que no traen uno propio en la Task. Los turnos
    # de conversación llevan el suyo en ``Task.approval`` y no mutan este servicio.
    approval: object | None = None
    cancel: object | None = None
    skills: object | None = None
    sandbox: object | None = None
    delegation: object | None = None


def approval_for(task: Task, services: RuntimeServices | None = None) -> object | None:
    """Puerto de aprobación del turno, con el servicio como fallback.

    El puerto viaja en la ``Task`` para que dos turnos concurrentes no se pisen. El
    valor de ``services`` sólo se usa en ejecuciones directas que no crean Task con
    aprobación propia (tests, runners invocados a mano, integraciones externas).
    """
    turn_approval = getattr(task, "approval", None)
    if turn_approval is not None:
        return turn_approval
    return getattr(services, "approval", None)


@dataclass
class PlanningResult:
    """Salida del planner: plan ejecutable, gap de capacidad o aclaración."""

    kind: str  # "plan" | "capability_gap" | "needs_clarification"
    graph: ExecutionGraph | None = None
    plan: ExecutionPlan | None = None
    gap: CapabilityGap | None = None
    question: str | None = None
    rationale: str = ""

    def to_dict(self) -> dict:
        """Serializa el resultado a dict JSON-serializable."""
        return {
            "kind": self.kind,
            "graph": self.graph.to_dict() if self.graph is not None else None,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "gap": self.gap.to_dict() if self.gap is not None else None,
            "question": self.question,
            "rationale": self.rationale,
        }


class Planner(ABC):
    """LLM → PlanningResult (plan | capability_gap | needs_clarification)."""

    @abstractmethod
    async def plan(
        self,
        task: Task,
        catalog: AgentRegistry,
        context_hint: tuple[ContextRef, ...] = (),
    ) -> PlanningResult:
        """Planea el grafo de ejecución para el task."""
        raise NotImplementedError


class GraphRunner(ABC):
    """Ejecuta A→B→C directo, sin pasar por el Orchestrator (Etapa 5)."""

    @abstractmethod
    async def run(
        self,
        graph: ExecutionGraph,
        task: Task,
        services: RuntimeServices,
    ) -> TaskResult:
        """Ejecuta el grafo y devuelve el resultado agregado."""
        raise NotImplementedError


class Orchestrator(ABC):
    """Global: inicia, supervisa, replanifica y finaliza (Etapa 6)."""

    @abstractmethod
    async def execute(self, task: Task) -> TaskResult:
        """Ejecuta una tarea de principio a fin."""
        raise NotImplementedError
