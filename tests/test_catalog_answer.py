"""Respuestas locales sobre el propio sistema (sin LLM y sin grafo)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.events import EventType
from satellite.core.spec import AgentSpec
from satellite.core.task import Task
from satellite.events.bus import EventBus
from satellite.orchestration.base import GraphRunner, Planner, RuntimeServices
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
