"""Tests del clasificador de consultas (chat-directo vs tarea)."""

import asyncio

from satellite.core.task import Task
from satellite.orchestration.chat_detector import (
    heuristic_is_chat,
    is_chat_goal,
)
from satellite.providers.fake import FakeLLM


def make_task(goal: str):
    from pathlib import Path

    return Task(goal=goal, project_root=Path(r"C:\Users\Ian\Desktop\Satellite"))


def test_heuristic_basic_greeting_is_chat() -> None:
    assert heuristic_is_chat("hola, ¿cómo estás?") is True
    assert heuristic_is_chat("¿qué es la fotosíntesis?") is True


def test_heuristic_task_hint_not_chat() -> None:
    assert heuristic_is_chat("crea una página html que diga hola") is False
    assert heuristic_is_chat("arregla el error en main.py") is False
    assert heuristic_is_chat("implementa una función que sume") is False


def test_heuristic_long_not_chat() -> None:
    assert heuristic_is_chat("x" * 200) is False


def test_heuristic_question_short_is_chat() -> None:
    assert heuristic_is_chat("¿Cuál es la capital de Francia?") is True


def test_is_chat_goal_trusts_heuristic_without_llm():
    """Chat claro (heurística) → NO consulta al LLM (modelos pequeños no fiables)."""

    class FakeChatLLM(FakeLLM):
        async def complete(self, request):
            from satellite.core.messages import LLMResponse

            self.requests.append(request)
            return LLMResponse(content="task")  # aunque el LLM diga task, la heurística manda

    provider = FakeChatLLM()
    task = make_task("¿qué es el infinitivo?")
    assert is_chat_goal(task, provider) is True
    # no se llamó al LLM (heurística decidió sola)
    assert len(provider.requests) == 0


def test_is_chat_goal_ambiguous_uses_llm() -> None:
    """Consulta corta sin pistas claras (ni chat ni tarea) → consulta al LLM."""
    provider = FakeLLM(script=[{"content": "chat"}])
    # "clima de bogota" no tiene pistas de chat ni de tarea, no termina en ? → LLM decide
    task = make_task("clima de bogota")
    assert is_chat_goal(task, provider) is True
    assert len(provider.requests) == 1


def test_is_chat_goal_task_hint_false_even_with_provider() -> None:
    provider = FakeLLM(script=[{"content": "chat"}])
    task = make_task("crea un archivo")
    assert is_chat_goal(task, provider) is False
    # no debe consultar al LLM (la pista de tarea es determinante)
    assert len(provider.requests) == 0


def test_is_chat_goal_without_provider_falls_to_heuristic() -> None:
    task = make_task("hola, ¿qué tal?")
    assert is_chat_goal(task, None) is True
    task2 = make_task("escribe una función en python")
    assert is_chat_goal(task2, None) is False
