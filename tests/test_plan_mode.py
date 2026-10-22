"""Tests del modo plan: alternativas propuestas por el LLM (Fase plan)."""

import asyncio
import json
from pathlib import Path

import pytest

from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.errors import SpecValidationError
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.plan import PlanOption, PlanOptions
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import GraphRunner, Planner, PlanningResult, RuntimeServices
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.orchestration.plan_mode import PlanOptionGenerator
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


# --- modelos ---

def make_options() -> PlanOptions:
    return PlanOptions(
        question="¿Cómo lo hacemos?",
        options=(
            PlanOption(
                id="minimo",
                title="Cambio mínimo",
                summary="Solo lo imprescindible.",
                approach=("Identificar el punto", "Editarlo"),
                effort="bajo",
                risk="bajo",
            ),
            PlanOption(
                id="robusto",
                title="Solución robusta",
                summary="Refactor previo y tests.",
                approach=("Refactor", "Tests", "Implementación"),
                tradeoffs="Más tiempo",
                effort="alto",
                risk="medio",
            ),
        ),
        recommendation="minimo",
    )


def test_options_roundtrip() -> None:
    options = make_options()
    assert PlanOptions.from_dict(options.to_dict()) == options


def test_recommended_resolves() -> None:
    options = make_options()
    assert options.recommended is not None
    assert options.recommended.id == "minimo"
    assert options.option("nope") is None


def test_option_validation() -> None:
    with pytest.raises(SpecValidationError):
        PlanOption(id="Bad Id", title="t", summary="s")
    with pytest.raises(SpecValidationError):
        PlanOption(id="ok", title="", summary="s")
    with pytest.raises(SpecValidationError):
        PlanOption(id="ok", title="t", summary="s", approach=(" ",))


def test_options_require_at_least_one() -> None:
    with pytest.raises(SpecValidationError):
        PlanOptions(question="q", options=())
    with pytest.raises(SpecValidationError):
        PlanOptions(question="", options=(PlanOption(id="a", title="t", summary="s"),))


def test_options_reject_duplicate_ids() -> None:
    with pytest.raises(SpecValidationError):
        PlanOptions(
            question="q",
            options=(
                PlanOption(id="a", title="t", summary="s"),
                PlanOption(id="a", title="t2", summary="s2"),
            ),
        )


# --- generador ---

def make_generator(script, **kwargs) -> PlanOptionGenerator:
    prompts = DefaultPromptRegistry()
    prompts.register_pair(
        "plan_options",
        "T:{task}\nC:{context}\nF:{feedback}\nS:{output_schema}",
        "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
    )
    return PlanOptionGenerator(FakeLLM(script=script), prompts, **kwargs)


def test_generator_parses_options() -> None:
    payload = {"question": "q", "options": [
        {"id": "a", "title": "A", "summary": "sa"},
        {"id": "b", "title": "B", "summary": "sb"},
    ]}
    generator = make_generator([{"content": json.dumps(payload)}])
    options = run(generator.generate("tarea", context="ctx"))
    assert options is not None
    assert [option.id for option in options.options] == ["a", "b"]


def test_generator_retries_on_invalid() -> None:
    payload = {"question": "q", "options": [
        {"id": "a", "title": "A", "summary": "sa"},
        {"id": "b", "title": "B", "summary": "sb"},
    ]}
    generator = make_generator([{"content": "no json"}, {"content": json.dumps(payload)}])
    options = run(generator.generate("tarea"))
    assert options is not None
    assert "JSON" in generator.provider.requests[1].messages[-1].content


def test_generator_rejects_too_few_options() -> None:
    payload = {"question": "q", "options": [{"id": "a", "title": "A", "summary": "s"}]}
    generator = make_generator([{"content": json.dumps(payload)}], max_attempts=1)
    assert run(generator.generate("tarea")) is None


def test_generator_returns_none_when_exhausted() -> None:
    generator = make_generator([{"content": "nada"}], max_attempts=1)
    assert run(generator.generate("tarea")) is None


# --- orquestador ---

class StubAdvisor:
    def __init__(self, options: PlanOptions | None = None) -> None:
        self.options = options
        self.calls = 0
        self.last_feedback = ""
        self.last_context = ""
        self.last_history = ""

    async def generate(self, task, *, context="", history="", feedback=""):
        self.calls += 1
        self.last_feedback = feedback
        self.last_context = context
        self.last_history = history
        return self.options


class ClarifyingPlanner(Planner):
    def __init__(self, kind: str = "needs_clarification") -> None:
        self.kind = kind
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        if self.kind == "needs_clarification":
            return PlanningResult(kind="needs_clarification", question="¿Google o email?")
        graph = ExecutionGraph(entry_node="s1")
        graph.add_node(Node(id="s1", spec_id="orchestrator", task={"goal": "x"}))
        return PlanningResult(kind="plan", graph=graph)


class OkRunner(GraphRunner):
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


def make_registry() -> DefaultAgentRegistry:
    registry = DefaultAgentRegistry()
    registry.register_spec(
        AgentSpec(id="orchestrator", name="O", capabilities=("plan_tasks",), prompt_ref="orchestrator")
    )
    return registry


class FailingAdvisor:
    """Asesor que revienta (p. ej. proveedor caído)."""

    def __init__(self, message: str = "sin conexión") -> None:
        self.message = message
        self.calls = 0

    async def generate(self, task, *, context="", history="", feedback=""):
        self.calls += 1
        raise RuntimeError(self.message)


class Sink:
    """Recoge los eventos emitidos (para comprobar que nada es silencioso)."""

    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)


def make_orchestrator(planner, advisor, *, runner=None, sink=None) -> DefaultOrchestrator:
    services = RuntimeServices(registry=make_registry())
    return DefaultOrchestrator(
        planner,
        runner or OkRunner(),
        services,
        plan_advisor=advisor,
        allow_chat_direct=False,
        sink=sink,
    )


def test_forced_plan_mode_returns_options(tmp_path: Path) -> None:
    advisor = StubAdvisor(make_options())
    orch = make_orchestrator(ClarifyingPlanner("plan"), advisor)
    task = Task(goal="agrega login", project_root=tmp_path, metadata={"plan_mode": True})
    result = run(orch.execute(task))
    assert result.output["mode"] == "plan_options"
    assert len(result.output["plan_options"]["options"]) == 2
    assert advisor.calls == 1


def test_ambiguity_triggers_options(tmp_path: Path) -> None:
    advisor = StubAdvisor(make_options())
    orch = make_orchestrator(ClarifyingPlanner("needs_clarification"), advisor)
    result = run(orch.execute(Task(goal="agrega login", project_root=tmp_path)))
    assert result.output["mode"] == "plan_options"
    # la duda del orquestador llega al asesor como feedback
    assert "Google o email" in advisor.last_feedback


def test_allow_plan_options_false_keeps_clarification(tmp_path: Path) -> None:
    advisor = StubAdvisor(make_options())
    orch = make_orchestrator(ClarifyingPlanner("needs_clarification"), advisor)
    task = Task(
        goal="agrega login",
        project_root=tmp_path,
        metadata={"allow_plan_options": False},
    )
    result = run(orch.execute(task))
    assert result.output["mode"] == "needs_clarification"
    assert advisor.calls == 0


def test_without_advisor_no_options(tmp_path: Path) -> None:
    orch = make_orchestrator(ClarifyingPlanner("needs_clarification"), None)
    result = run(orch.execute(Task(goal="x", project_root=tmp_path)))
    assert result.output["mode"] == "needs_clarification"


def test_advisor_failure_falls_back(tmp_path: Path) -> None:
    advisor = StubAdvisor(None)
    orch = make_orchestrator(ClarifyingPlanner("needs_clarification"), advisor)
    result = run(orch.execute(Task(goal="x", project_root=tmp_path)))
    assert result.output["mode"] == "needs_clarification"


# --- el modo plan ya no desaparece en silencio ---


def test_forced_plan_failure_is_loud(tmp_path: Path) -> None:
    """Plan pedido y asesor caído: error legible + evento (antes: silencio)."""
    from satellite.core.events import EventType

    sink = Sink()
    advisor = FailingAdvisor("sin conexión")
    orch = make_orchestrator(ClarifyingPlanner("plan"), advisor, sink=sink)
    task = Task(goal="agrega login", project_root=tmp_path, metadata={"plan_mode": True})

    result = run(orch.execute(task))

    assert result.status is TaskStatus.FAILED
    assert "sin conexión" in (result.error or "")
    assert "modo plan" in (result.error or "")
    plan_events = [e for e in sink.events if e.event_type is EventType.PLAN_FAILED]
    assert len(plan_events) == 1
    assert plan_events[0].payload["kind"] == "error"
    assert "sin conexión" in plan_events[0].payload["reason"]


def test_forced_plan_without_options_says_so(tmp_path: Path) -> None:
    from satellite.core.events import EventType

    sink = Sink()
    orch = make_orchestrator(ClarifyingPlanner("plan"), StubAdvisor(None), sink=sink)
    task = Task(goal="agrega login", project_root=tmp_path, metadata={"plan_mode": True})

    result = run(orch.execute(task))

    assert result.status is TaskStatus.NEEDS_USER_INPUT
    assert "alternativas" in (result.error or "")
    plan_events = [e for e in sink.events if e.event_type is EventType.PLAN_FAILED]
    assert len(plan_events) == 1
    assert plan_events[0].payload["kind"] == "empty"


def test_ambiguity_failure_keeps_the_fallback_but_leaves_a_trace(tmp_path: Path) -> None:
    """Mejora automática: el flujo no cambia, pero deja de ser invisible."""
    from satellite.core.events import EventType

    sink = Sink()
    orch = make_orchestrator(
        ClarifyingPlanner("needs_clarification"), FailingAdvisor(), sink=sink
    )
    result = run(orch.execute(Task(goal="x", project_root=tmp_path)))

    assert result.output["mode"] == "needs_clarification"
    plan_events = [e for e in sink.events if e.event_type is EventType.PLAN_FAILED]
    assert len(plan_events) == 1
    assert "sin conexión" in plan_events[0].payload["reason"]


# --- conversación ---

class StubOrchestrator:
    def __init__(self) -> None:
        self.tasks: list = []
        self.services = None

    async def execute(self, task):
        self.tasks.append(task)
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.NEEDS_USER_INPUT,
            summary="elige",
            output={"mode": "plan_options", "plan_options": make_options().to_dict()},
        )


def test_send_plan_mode_sets_metadata(tmp_path: Path) -> None:
    orchestrator = StubOrchestrator()
    chat = ChatSession(tmp_path, orchestrator)
    reply = run(chat.send("agrega login", plan_mode=True))
    assert reply.output["mode"] == "plan_options"
    assert orchestrator.tasks[0].metadata["plan_mode"] is True
    assert "alternativas" in reply.text


def test_choose_sends_followup_without_options(tmp_path: Path) -> None:
    orchestrator = StubOrchestrator()
    chat = ChatSession(tmp_path, orchestrator)
    option = make_options().options[0]
    run(chat.choose(option))
    assert orchestrator.tasks[0].metadata["allow_plan_options"] is False
    assert orchestrator.tasks[0].metadata["execute_choice"] is True
    assert "Cambio mínimo" in orchestrator.tasks[0].goal


class PlanThenOkOrchestrator:
    """Ofrece alternativas y, si la tarea es una elección, la ejecuta."""

    def __init__(self, options: PlanOptions) -> None:
        self.options = options
        self.tasks: list = []
        self.services = None

    async def execute(self, task):
        self.tasks.append(task)
        if task.metadata.get("execute_choice"):
            return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="en marcha")
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.NEEDS_USER_INPUT,
            summary="elige",
            output={"mode": "plan_options", "plan_options": self.options.to_dict()},
        )


def test_accepting_the_recommended_option_does_not_ask_again(tmp_path: Path) -> None:
    """Regresión: aceptar "la recomendada" en texto libre ejecuta, no re-pregunta.

    Tras el primer turno ambiguo («Audítalo») con una alternativa marcada como
    recomendada, un seguimiento que la acepta («La recomendada pero crea el agente que
    audita») es una orden de ejecución: ni catálogo, ni charla, ni una segunda ronda de
    alternativas.
    """
    options = make_options()  # recomendación = "minimo" (Cambio mínimo)
    orchestrator = PlanThenOkOrchestrator(options)
    chat = ChatSession(tmp_path, orchestrator)

    first = run(chat.send("Audítalo"))
    assert first.output["mode"] == "plan_options"

    second = run(chat.send("La recomendada pero crea el agente que audita"))
    task = orchestrator.tasks[1]
    assert task.metadata.get("execute_choice") is True
    assert task.metadata.get("allow_plan_options") is False
    assert "Cambio mínimo" in task.goal  # la alternativa recomendada, resuelta
    assert second.output.get("mode") != "plan_options"


def test_followup_not_accepting_the_recommendation_is_left_alone(tmp_path: Path) -> None:
    """Un seguimiento que no acepta la recomendada no se convierte en elección."""
    orchestrator = PlanThenOkOrchestrator(make_options())
    chat = ChatSession(tmp_path, orchestrator)
    run(chat.send("Audítalo"))
    run(chat.send("mejor pensemos otra cosa"))
    assert orchestrator.tasks[1].metadata.get("execute_choice") is None


class _PlannerQueRevienta:
    """Si el orquestador llega a planificar, es que no atajó por el catálogo."""

    async def plan(self, task, registry):
        raise RuntimeError("planificado")


class _SkillsVacias:
    """Doble del registro de skills: el catálogo usa `.all()`."""

    def all(self):
        return []


def _orchestrator_sin_proveedor(planner) -> DefaultOrchestrator:
    from types import SimpleNamespace

    services = SimpleNamespace(provider=None, skills=_SkillsVacias(), registry=None)
    return DefaultOrchestrator(planner, object(), services)


def test_a_chosen_option_is_not_answered_from_the_catalog(tmp_path: Path) -> None:
    """Elegir una alternativa es una orden de ejecución, no una pregunta al catálogo.

    Antes, la frase que manda `ChatSession.choose` («Opción elegida: … Procede con
    esta opción») casaba con la intención de catálogo y el usuario recibía «Tengo N
    skills guardadas» con un task_finished, sin ejecutar nada.
    """
    from satellite.core.task import Task

    # control: sin la marca, el catálogo contesta (y no se planifica)
    placer = _orchestrator_sin_proveedor(_PlannerQueRevienta())
    respuesta = run(placer.execute(Task(goal="¿qué skills tienes?", project_root=tmp_path)))
    assert respuesta.output.get("mode") == "direct_reply"
    assert "skills guardadas" in respuesta.summary

    # con la marca de "opción elegida", sigue el camino normal: planifica
    marcado = _orchestrator_sin_proveedor(_PlannerQueRevienta())
    with pytest.raises(RuntimeError, match="planificado"):
        run(
            marcado.execute(
                Task(
                    goal="¿qué skills tienes?",
                    project_root=tmp_path,
                    metadata={"execute_choice": True},
                )
            )
        )
