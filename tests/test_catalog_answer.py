"""Respuestas locales sobre el propio sistema (sin LLM y sin grafo)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.events import EventType
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.plan import CapabilityGap
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.events.bus import EventBus
from satellite.orchestration.base import (
    GraphRunner,
    Planner,
    PlanningResult,
    RuntimeServices,
)
from satellite.orchestration.catalog_answer import (
    AGENTS,
    MODEL,
    SKILLS,
    TOOLS,
    answer_catalog_question,
    catalog_intent,
)
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.providers.fake import FakeLLM
from satellite.skills.registry import SkillRegistry


def make_spec(spec_id: str, capabilities=("do_thing",)) -> AgentSpec:
    return AgentSpec(
        id=spec_id,
        name=spec_id.replace("_", " ").title(),
        description=f"hace {spec_id}",
        capabilities=capabilities,
        prompt_ref=spec_id,
        tools=(),
    )


class FakeTools:
    """Registro de herramientas mínimo (sólo ``all()``)."""

    def __init__(self, tools=()) -> None:
        self._tools = list(tools)

    def all(self) -> list:
        return list(self._tools)


def make_services(tmp_path: Path, *, tools=None, provider=None) -> RuntimeServices:
    registry = DefaultAgentRegistry(
        {spec.id: spec for spec in (make_spec("orchestrator"), make_spec("agent_creator"))}
    )
    return RuntimeServices(
        registry=registry,
        tools=FakeTools(tools or []),
        skills=SkillRegistry(tmp_path),
        provider=provider,
    )


# ---------------------------------------------------------------- intención


def test_catalog_intent_detects_the_kind() -> None:
    assert catalog_intent("¿Qué agentes tienes?") == AGENTS
    assert catalog_intent("¿qué skills hay guardadas?") == SKILLS
    assert catalog_intent("¿qué herramientas tienes?") == TOOLS
    assert catalog_intent("¿qué modelo eres?") == MODEL
    assert catalog_intent("lista los agentes") == AGENTS
    assert catalog_intent("crea un archivo") == ""
    assert catalog_intent("¿qué es la fotosíntesis?") == ""
    assert catalog_intent("") == ""


def test_relative_que_does_not_make_it_a_catalog_question(tmp_path: Path) -> None:
    """Regresión: el "que" relativo ("el agente que audita") no es una pregunta.

    Antes, "La recomendada pero crea el agente que audita" casaba con la intención de
    catálogo (por el "que" + "agente") y el usuario recibía «Tengo N agentes
    disponibles» en vez de crear el agente que pedía.
    """
    assert catalog_intent("La recomendada pero crea el agente que audita") == ""
    assert catalog_intent("No me referia que tipo de agentes tenia") == ""
    assert catalog_intent("crea el agente que audita") == ""
    assert (
        answer_catalog_question(
            "La recomendada pero crea el agente que audita", make_services(tmp_path)
        )
        is None
    )


def test_action_request_is_not_a_catalog_question() -> None:
    """Una orden de trabajo con acción no se contesta con el catálogo."""
    assert catalog_intent("dime cómo crear el agente que audita") == ""
    assert catalog_intent("genera un agente que audite el repo") == ""


def test_true_catalog_questions_are_preserved(tmp_path: Path) -> None:
    """Las preguntas de verdad siguen contestándose desde el catálogo real."""
    assert catalog_intent("¿qué agentes tienes?") == AGENTS
    assert catalog_intent("muéstrame los agentes") == AGENTS
    assert catalog_intent("¿cuáles son tus agentes?") == AGENTS
    assert catalog_intent("dime los agentes") == AGENTS
    assert catalog_intent("qué agentes sirven para crear html") == AGENTS
    text = answer_catalog_question("¿qué agentes tienes?", make_services(tmp_path))
    assert text is not None and "2 agentes" in text


# ---------------------------------------------------------------- respuestas


def test_agents_answer_lists_the_real_catalog(tmp_path: Path) -> None:
    text = answer_catalog_question("¿Qué agentes tienes?", make_services(tmp_path))
    assert text is not None
    assert "2 agentes" in text
    assert "- agent_creator:" in text
    assert "- orchestrator:" in text
    assert "no agentes ni skills" in text  # aclara que las primitivas no son agentes


def test_skills_answer_explains_the_difference(tmp_path: Path) -> None:
    text = answer_catalog_question("¿qué skills tienes?", make_services(tmp_path))
    assert text is not None
    assert "no hay skills" in text
    assert "no son skills" in text


def test_tools_answer_marks_kernel_primitives(tmp_path: Path) -> None:
    tools = [
        SimpleNamespace(id="run_python", description="ejecuta python"),
        SimpleNamespace(id="git_status", description="estado de git"),
    ]
    text = answer_catalog_question("¿qué herramientas tienes?", make_services(tmp_path, tools=tools))
    assert text is not None
    assert "run_python" in text
    assert "git_status" in text
    # todas las del runtime son del kernel y no se pueden suplantar
    assert "Todas son del kernel" in text
    assert "reservados" in text


def test_model_answer_uses_the_configured_provider(tmp_path: Path) -> None:
    provider = SimpleNamespace(model="nemotron", provider_name="openai")
    text = answer_catalog_question("¿qué modelo eres?", make_services(tmp_path, provider=provider))
    assert text is not None
    assert "nemotron" in text and "openai" in text
    # sin proveedor lo dice claro
    text = answer_catalog_question("¿qué modelo eres?", make_services(tmp_path))
    assert text is not None and "No hay proveedor" in text


# ---------------------------------------------------------------- idioma de respuesta


def test_agents_answer_in_english(tmp_path: Path) -> None:
    text = answer_catalog_question("What agents do you have?", make_services(tmp_path))
    assert text is not None
    assert "agents available" in text
    assert "- agent_creator:" in text and "- orchestrator:" in text
    assert "not agents or skills" in text
    # sin mezcla de español en la respuesta inglesa
    assert "Tengo" not in text and "agentes disponibles" not in text


def test_skills_answer_in_english(tmp_path: Path) -> None:
    text = answer_catalog_question("what skills do you have?", make_services(tmp_path))
    assert text is not None
    assert "no saved skills" in text
    assert "not skills" in text
    assert "guardadas" not in text


def test_tools_answer_in_english(tmp_path: Path) -> None:
    tools = [SimpleNamespace(id="run_python", description="runs python")]
    text = answer_catalog_question("what tools do you have?", make_services(tmp_path, tools=tools))
    assert text is not None
    assert "run_python" in text
    assert "kernel tools" in text
    assert "reservados" not in text


def test_model_answer_in_english(tmp_path: Path) -> None:
    provider = SimpleNamespace(model="nemotron", provider_name="openai")
    text = answer_catalog_question(
        "What model are you?", make_services(tmp_path, provider=provider)
    )
    assert text is not None
    assert "I use the model 'nemotron' through the provider 'openai'." in text
    # sin proveedor, también en inglés
    none_text = answer_catalog_question("What model are you?", make_services(tmp_path))
    assert none_text is not None and "No LLM provider" in none_text


def test_agents_answer_defaults_to_spanish_when_ambiguous(tmp_path: Path) -> None:
    text = answer_catalog_question("agentes?", make_services(tmp_path))
    assert text is not None
    assert "agentes disponibles" in text  # sin señal → idioma de producto (es)


def test_non_catalog_question_is_not_hijacked(tmp_path: Path) -> None:
    assert answer_catalog_question("¿qué es la fotosíntesis?", make_services(tmp_path)) is None
    assert answer_catalog_question("crea un archivo", make_services(tmp_path)) is None


# ---------------------------------------------------------------- integración


class NeverPlanner(Planner):
    """Planner que falla si le llega una pregunta de catálogo."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, registry, context_hint=()):
        self.calls += 1
        raise AssertionError("una pregunta de catálogo no debe planificarse")


class NeverRunner(GraphRunner):
    """Runner que falla si le llega un grafo."""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, graph, task, services):
        self.calls += 1
        raise AssertionError("una pregunta de catálogo no debe ejecutar grafo")


def test_orchestrator_answers_catalog_questions_without_planning(tmp_path: Path) -> None:
    """El bug original: "¿Qué puedes hacer?" generaba un grafo y sus eventos."""
    bus = EventBus()
    events: list = []
    bus.subscribe(events.append)
    provider = FakeLLM()
    planner, runner = NeverPlanner(), NeverRunner()
    orchestrator = DefaultOrchestrator(
        planner, runner, make_services(tmp_path, provider=provider), sink=bus
    )

    # Pregunta de catálogo → respuesta LOCAL: ni LLM ni grafo
    task = Task(goal="¿Qué agentes tienes disponibles?", project_root=tmp_path)
    result = asyncio.run(orchestrator.execute(task))
    assert result.output["mode"] == "direct_reply"
    assert "orchestrator" in result.summary
    assert provider.requests == []  # no gastó ninguna llamada al modelo

    # Pregunta meta sin catálogo → respuesta directa del LLM, pero SIN grafo
    task = Task(goal="¿Qué puedes hacer?", project_root=tmp_path)
    result = asyncio.run(orchestrator.execute(task))
    assert result.output["mode"] == "direct_reply"
    assert len(provider.requests) == 1

    assert planner.calls == 0
    assert runner.calls == 0
    kinds = [event.event_type for event in events]
    # ningún evento de ejecución: sólo inicio y fin de tarea
    assert EventType.GRAPH_BUILT not in kinds
    assert EventType.NODE_STARTED not in kinds
    assert kinds.count(EventType.TASK_FINISHED) == 2


class GapThenPlanPlanner(Planner):
    """Primera pasada: falta una capacidad; tras crearla, un plan ejecutable."""

    def __init__(self, missing: str = "auditar el proyecto") -> None:
        self.missing = missing
        self.goals: list[str] = []

    async def plan(self, task, registry, context_hint=()):
        self.goals.append(task.goal)
        if len(self.goals) == 1:
            return PlanningResult(
                kind="capability_gap", gap=CapabilityGap(missing=self.missing)
            )
        graph = ExecutionGraph(entry_node="s1")
        graph.add_node(Node(id="s1", spec_id="orchestrator", task={"goal": "auditar"}))
        return PlanningResult(kind="plan", graph=graph)


class RecordingExpander:
    """Expander falso: registra la necesidad y dice que creó el agente."""

    def __init__(self) -> None:
        self.needs: list[str] = []

    async def expand(self, need, *, task_id="", approval_port=None):
        self.needs.append(need)
        return SimpleNamespace(
            kind="created", spec=SimpleNamespace(id="auditor_de_proyecto")
        )


class CountingOkRunner(GraphRunner):
    """Runner que ejecuta el grafo y responde OK."""

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(
            task_id=task.task_id, status=TaskStatus.OK, summary="auditoría hecha"
        )


def test_followup_creating_an_agent_is_not_answered_from_the_catalog(
    tmp_path: Path,
) -> None:
    """Regresión del flujo reportado: "La recomendada pero crea el agente que audita".

    Antes se contestaba «Tengo N agentes disponibles» (catálogo). Ahora llega al
    planificador como tarea, la capacidad faltante se cubre creando el agente y el
    plan se ejecuta.
    """
    planner = GapThenPlanPlanner()
    runner = CountingOkRunner()
    expander = RecordingExpander()
    orchestrator = DefaultOrchestrator(
        planner,
        runner,
        make_services(tmp_path),  # sin proveedor: no hay charla ni clasificación LLM
        expander=expander,
    )

    goal = "La recomendada pero crea el agente que audita"
    result = asyncio.run(orchestrator.execute(Task(goal=goal, project_root=tmp_path)))

    # No lo atajó el catálogo: llegó al planificador tal cual y se pidió el agente.
    assert planner.goals[0] == goal
    assert expander.needs == ["auditar el proyecto"]
    assert runner.runs == 1
    assert result.status is TaskStatus.OK
    assert "agentes disponibles" not in (result.summary or "")
