"""Regresión: "de qué trata el proyecto" es conversación, nunca una tarea.

Antes, la variante natural sin "?" (y sin abrir con palabra interrogativa) no la
reconocía la heurística, así que caía al clasificador LLM y, al contestar "task",
acababa en `LLMPlanner` → "could not generate a valid plan after 2 attempts".

Se cubre: clasificación de las variantes ES/EN, que **no** se invoca el planner ni el
runner, que se usa el contexto de proyecto disponible y que, si el índice está vacío,
se dice explícitamente en vez de inventar.
"""

import asyncio
from pathlib import Path

from satellite.core.messages import LLMResponse
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import (
    GraphRunner,
    Planner,
    RuntimeServices,
)
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.chat_detector import (
    has_task_hint,
    heuristic_is_chat,
    is_chat_goal,
    is_project_overview,
)
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.providers.fake import FakeLLM

OVERVIEW_VARIANTS = (
    "De que trata el proyecto",
    "¿De qué trata el proyecto?",
    "de qué trata este proyecto",
    "en qué consiste el proyecto",
    "de qué va el repo",
    "What is the project about?",
    "What does this repo do?",
)


def run(coro):
    return asyncio.run(coro)


class PlannerMustNotBeCalled(Planner):
    """Planner que revienta si se invoca: un overview no debe planificarse."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        raise AssertionError("LLMPlanner no debe invocarse para un overview conversacional")


class RunnerMustNotRun(GraphRunner):
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="grafo")


def make_orchestrator(provider):
    planner = PlannerMustNotBeCalled()
    runner = RunnerMustNotRun()
    services = RuntimeServices(provider=provider)
    return DefaultOrchestrator(planner, runner, services), planner, runner


# --------------------------------------------------------------- clasificación


def test_overview_variants_are_classified_as_chat() -> None:
    for text in OVERVIEW_VARIANTS:
        assert is_project_overview(text) is True, text
        assert has_task_hint(text) is False, text
        assert heuristic_is_chat(text) is True, text


def test_overview_never_consults_the_llm_classifier(tmp_path: Path) -> None:
    """Aunque el clasificador diga "task", la heurística manda para el overview."""

    class ChatSaysTask(FakeLLM):
        async def complete(self, request):
            self.requests.append(request)
            return LLMResponse(content="task")

    provider = ChatSaysTask()
    task = Task(goal="De que trata el proyecto", project_root=tmp_path)
    assert is_chat_goal(task, provider) is True
    assert len(provider.requests) == 0  # no se gastó una llamada al LLM


def test_action_on_the_project_is_still_a_task() -> None:
    """El enrutado normal de tareas se conserva: acción + contexto = tarea."""
    assert heuristic_is_chat("arregla el proyecto") is False
    assert heuristic_is_chat("documenta el proyecto") is False
    assert heuristic_is_chat("crea un agente que resuma el proyecto") is False


# --------------------------------------------------------------- orquestador


def test_overview_never_invokes_planner_or_runner(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Satellite\nFramework de microagentes.", encoding="utf-8"
    )
    provider = FakeLLM(default_content="Satellite es un framework de microagentes.")
    orch, planner, runner = make_orchestrator(provider)

    result = run(orch.execute(Task(goal="De que trata el proyecto", project_root=tmp_path)))

    assert planner.calls == 0
    assert runner.runs == 0
    assert result.status is TaskStatus.OK
    assert result.output["mode"] == "direct_reply"
    assert result.output["content"] == "Satellite es un framework de microagentes."


def test_overview_uses_available_project_context(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Proyecto Centinela\nUn satélite de prueba.", encoding="utf-8"
    )
    provider = FakeLLM(default_content="ok")
    orch, planner, _ = make_orchestrator(provider)

    task = Task(goal="De que trata el proyecto", project_root=tmp_path)
    run(orch.execute(task))

    assert planner.calls == 0
    assert task.metadata["project_context_status"] == "ok"
    prompt = provider.requests[-1].messages[-1].content
    assert "Proyecto Centinela" in prompt
    assert "PROJECT CONTEXT" in prompt


def test_overview_empty_index_is_stated_not_invented(tmp_path: Path) -> None:
    """Sin índice no se llama al modelo: se dice que no hay contexto, y nada inventado."""
    provider = FakeLLM(default_content="contenido inventado")
    orch, planner, _ = make_orchestrator(provider)

    es = run(ChatSession(tmp_path, orch, persist=False).send("De que trata el proyecto"))
    assert planner.calls == 0
    assert "No hay contexto de proyecto que adjuntar" in es.text
    assert "contenido inventado" not in es.text
    assert len(provider.requests) == 0

    en = run(ChatSession(tmp_path, orch, persist=False).send("What does this repo do?"))
    assert planner.calls == 0
    assert "There is no project context to attach" in en.text
    assert "contenido inventado" not in en.text
    assert len(provider.requests) == 0
