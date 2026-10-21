"""Preflight de capacidades: la falta de un agente se anuncia ANTES de ejecutar.

Regresión del flujo `code_explainer`: antes, un plan que pedía un agente no disponible
solo se descubría al ejecutar (o al final, tras un fallo + replan). Ahora la
disponibilidad se comprueba de entrada y, si falta, se responde con opciones concretas
sin lanzar ningún nodo.
"""

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.agents.expander import ExpansionOutcome
from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import (
    GraphRunner,
    Planner,
    PlanningResult,
    RuntimeServices,
)
from satellite.orchestration.orchestrator import DefaultOrchestrator


def run(coro):
    return asyncio.run(coro)


class MissingCapabilityPlanner(Planner):
    """Plan que referencia un agente ausente del catálogo."""

    def __init__(self, spec_id: str = "auditor_de_proyecto") -> None:
        self.spec_id = spec_id
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        graph = ExecutionGraph(entry_node="s1")
        graph.add_node(Node(id="s1", spec_id=self.spec_id, task={"goal": "auditar"}))
        return PlanningResult(kind="plan", graph=graph)


class CountingRunner(GraphRunner):
    def __init__(self, status: str = "ok") -> None:
        self.status = status
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.OK if self.status == "ok" else TaskStatus.FAILED,
            summary="ok",
        )


class StubAgent(Agent):
    """Agente mínimo que sólo existe para que la fábrica pueda construirlo."""

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(node_id=ctx.node_id, spec_id="stub", status=AgentStatus.OK)


class SelectiveFactory(AgentFactory):
    """Fábrica que sabe construir sólo los ids indicados y falla en el resto.

    Simula el caso real de una fábrica que no puede armar el agente por falta de
    prompt o de dependencia, aunque el spec sí esté en el registro.
    """

    def __init__(self, can_build: set[str]) -> None:
        self.can_build = set(can_build)

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        if spec_id not in self.can_build:
            raise RuntimeError(f"no se puede construir '{spec_id}' (falta prompt/dependencia)")
        return StubAgent()


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        pass


def make_registry() -> DefaultAgentRegistry:
    reg = DefaultAgentRegistry()
    reg.register_spec(
        AgentSpec(
            id="code_explainer",
            name="Code Explainer",
            capabilities=("explicar código",),
            prompt_ref="code_explainer",
        )
    )
    return reg


def make_task(tmp_path: Path) -> Task:
    return Task(goal="audita el proyecto Planos/", project_root=tmp_path)


def test_missing_capability_is_announced_before_running(tmp_path: Path) -> None:
    runner = CountingRunner()
    services = RuntimeServices(registry=make_registry())
    orch = DefaultOrchestrator(MissingCapabilityPlanner(), runner, services)

    result = run(orch.execute(make_task(tmp_path)))

    assert runner.runs == 0  # no se ejecutó ningún nodo
    assert result.status is TaskStatus.NEEDS_USER_INPUT
    assert result.output["mode"] == "capability_gap"
    assert result.output["capability_gap"]["missing"] == "auditor_de_proyecto"
    assert result.output["preflight"] is True
    assert result.output["options"]
    assert "auditor_de_proyecto" in result.summary


def test_available_capability_runs_normally(tmp_path: Path) -> None:
    runner = CountingRunner()
    services = RuntimeServices(registry=make_registry())
    orch = DefaultOrchestrator(
        MissingCapabilityPlanner(spec_id="code_explainer"), runner, services
    )

    result = run(orch.execute(make_task(tmp_path)))

    assert runner.runs == 1
    assert result.status is TaskStatus.OK


def test_preflight_emits_plan_failed_event(tmp_path: Path) -> None:
    sink = RecordingSink()
    services = RuntimeServices(registry=make_registry())
    orch = DefaultOrchestrator(MissingCapabilityPlanner(), CountingRunner(), services, sink=sink)

    run(orch.execute(make_task(tmp_path)))

    kinds = [e.payload.get("kind") for e in sink.events if e.event_type.value == "plan_failed"]
    assert "capability_gap_preflight" in kinds


def test_registered_spec_rejected_by_factory_is_announced_before_running(
    tmp_path: Path,
) -> None:
    """El spec está en el registro, pero la fábrica no lo puede construir.

    El preflight no debe fiarse del registro: valida con la fábrica que usará el runner,
    así que un spec conocido pero inviable (falta prompt/dependencia) se anuncia de
    entrada, sin ejecutar ningún nodo.
    """
    sink = RecordingSink()
    runner = CountingRunner()
    services = RuntimeServices(
        registry=make_registry(),  # registra 'code_explainer'
        factory=SelectiveFactory(can_build=set()),  # no puede construir nada
    )
    orch = DefaultOrchestrator(
        MissingCapabilityPlanner(spec_id="code_explainer"), runner, services, sink=sink
    )

    result = run(orch.execute(make_task(tmp_path)))

    assert runner.runs == 0  # no se ejecutó ningún nodo
    assert result.status is TaskStatus.NEEDS_USER_INPUT
    assert result.output["mode"] == "capability_gap"
    assert result.output["capability_gap"]["missing"] == "code_explainer"
    assert result.output["preflight"] is True
    assert "code_explainer" in result.summary
    kinds = [e.payload.get("kind") for e in sink.events if e.event_type.value == "plan_failed"]
    assert "capability_gap_preflight" in kinds


class ClarifyingExpander:
    """El creador no puede decidir sin preguntar al usuario."""

    async def expand(
        self,
        need,
        *,
        task_id="",
        approval_port=None,
        extra_context="",
        user_language=None,
    ):
        return ExpansionOutcome(
            kind="needs_clarification",
            question="¿Qué formato debe tener el informe?",
        )


def test_preflight_propagates_creator_clarification(tmp_path: Path) -> None:
    runner = CountingRunner()
    services = RuntimeServices(registry=make_registry())
    orch = DefaultOrchestrator(
        MissingCapabilityPlanner(),
        runner,
        services,
        expander=ClarifyingExpander(),
    )

    result = run(orch.execute(make_task(tmp_path)))

    assert runner.runs == 0
    assert result.status is TaskStatus.NEEDS_USER_INPUT
    assert result.output["mode"] == "needs_clarification"
    assert "formato" in result.output["question"].lower()


def test_factory_building_unregistered_spec_is_not_a_gap(tmp_path: Path) -> None:
    """Un agente fuera del registro que la fábrica sí sabe construir no es un gap."""
    runner = CountingRunner()
    services = RuntimeServices(
        registry=make_registry(),  # no registra 'auditor_de_proyecto'
        factory=SelectiveFactory(can_build={"auditor_de_proyecto"}),
    )
    orch = DefaultOrchestrator(MissingCapabilityPlanner(), runner, services)

    result = run(orch.execute(make_task(tmp_path)))

    assert runner.runs == 1
    assert result.status is TaskStatus.OK
