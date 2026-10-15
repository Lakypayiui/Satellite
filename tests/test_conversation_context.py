"""La conversación reciente como contexto del agente.

Sin ella, un seguimiento como «explícamela» llegaba sin antecedente: la respuesta
directa contestaba una obviedad y los nodos del grafo no sabían a qué se refería.
"""

import asyncio
from pathlib import Path

from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.context_models import ContextBundle, ContextRefKind
from satellite.core.task import Task
from satellite.orchestration.base import GraphRunner, Planner, RuntimeServices
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.orchestration.runner import _with_conversation
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


class _PlannerQueNoPlanifica(Planner):
    async def plan(self, task, catalog, context_hint=()):
        raise AssertionError("una respuesta directa no debe planificar")


class _RunnerQueNoCorre(GraphRunner):
    async def run(self, graph, task, services):
        raise AssertionError("una respuesta directa no debe ejecutar el grafo")


def _orchestrator(provider) -> DefaultOrchestrator:
    services = RuntimeServices(registry=DefaultAgentRegistry(), provider=provider)
    return DefaultOrchestrator(_PlannerQueNoPlanifica(), _RunnerQueNoCorre(), services)


def _historia(*turnos: tuple[str, str]) -> list[dict]:
    return [
        {"role": role, "content": content, "task_id": "t", "status": "ok"}
        for role, content in turnos
    ]


def _enviado(provider: FakeLLM) -> str:
    return "\n".join(message.content for message in provider.requests[-1].messages)


# --- respuesta directa ---


def test_a_follow_up_reaches_the_model_with_the_previous_turns(tmp_path: Path) -> None:
    """«Explícamela» tiene que llegar con la lista anterior en el prompt."""
    provider = FakeLLM(script=[{"content": "La carpeta contiene tres archivos: ..."}])
    orch = _orchestrator(provider)
    task = Task(
        goal="explícamela",
        project_root=tmp_path,
        metadata={
            "history": _historia(
                ("user", "lista la carpeta Server"),
                ("assistant", "a.txt, b.txt, notas.md"),
            )
        },
    )

    result = run(orch._answer_direct(task))

    assert result.output["mode"] == "direct_reply"
    enviado = _enviado(provider)
    assert "CONVERSACIÓN RECIENTE" in enviado
    assert "a.txt, b.txt, notas.md" in enviado
    assert "explícamela" in enviado


def test_without_history_there_is_no_conversation_block(tmp_path: Path) -> None:
    """El primer turno no paga tokens de conversación que no existe."""
    provider = FakeLLM(script=[{"content": "hola"}])
    orch = _orchestrator(provider)

    run(orch._answer_direct(Task(goal="hola", project_root=tmp_path)))

    assert "CONVERSACIÓN RECIENTE" not in _enviado(provider)


# --- nodos del grafo ---


def test_the_node_context_carries_the_conversation(tmp_path: Path) -> None:
    """El nodo la recibe como sección dinámica, delante del resto del contexto."""
    task = Task(
        goal="haz un resumen de eso",
        project_root=tmp_path,
        metadata={"history": _historia(("assistant", "a.txt, b.txt, notas.md"))},
    )

    bundle = _with_conversation(ContextBundle(), task)

    assert [section.ref.kind for section in bundle.sections] == [ContextRefKind.CONVERSATION]
    assert "a.txt, b.txt, notas.md" in bundle.sections[0].content


def test_no_history_means_no_extra_section(tmp_path: Path) -> None:
    task = Task(goal="hola", project_root=tmp_path)
    assert _with_conversation(ContextBundle(), task).sections == ()
