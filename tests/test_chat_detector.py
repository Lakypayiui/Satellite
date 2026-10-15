"""Tests del clasificador de consultas (chat-directo vs tarea)."""

import asyncio
from pathlib import Path

from satellite.core.task import Task
from satellite.orchestration.chat_detector import (
    has_project_context,
    has_task_hint,
    heuristic_is_chat,
    is_chat_goal,
)
from satellite.providers.fake import FakeLLM


def make_task(goal: str, root: Path) -> Task:
    """Tarea con una raíz real (temporal): nada depende del repo del usuario."""
    return Task(goal=goal, project_root=root)


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


def test_heuristic_meta_questions_about_the_system_are_chat() -> None:
    """"¿Qué puedes hacer?" se planificaba por el verbo "hacer" (bug real)."""
    assert heuristic_is_chat("¿Qué puedes hacer?") is True
    assert heuristic_is_chat("¿Qué agentes tienes disponibles?") is True
    assert heuristic_is_chat("¿Qué skills hay guardadas?") is True
    assert heuristic_is_chat("¿Qué herramientas tienes?") is True
    assert heuristic_is_chat("¿Qué modelo eres?") is True
    assert heuristic_is_chat("¿Quién eres?") is True


def test_heuristic_ignores_trailing_whitespace() -> None:
    assert heuristic_is_chat("¿Qué agentes tienes?   ") is True
    assert heuristic_is_chat("\n ¿Puedes ayudarme? ") is True


def test_task_hint_needs_action_plus_project_context() -> None:
    # acción sin contexto de proyecto → no es tarea
    assert has_task_hint("haz lo que puedas") is False
    # acción + contexto → tarea
    assert has_task_hint("crea un archivo") is True
    assert has_task_hint("actualiza el README del proyecto") is True
    assert has_task_hint("crea un agente que resuma documentos") is True
    assert has_task_hint("arregla el fallo del script de python") is True


def test_heuristic_keeps_real_tasks_as_tasks() -> None:
    assert heuristic_is_chat("crea una página html que diga hola") is False
    assert heuristic_is_chat("crea un archivo con hola") is False
    assert heuristic_is_chat("escribe tests para el módulo de store") is False
    assert heuristic_is_chat("¿puedes crear un script que sume?") is False


# --- casos límite (bloque 1 del catálogo de comportamientos raros) ---


def test_help_requests_and_filler_are_chat() -> None:
    """"ayúdame" y el relleno no deben acabar en un grafo."""
    for text in (
        "ayúdame con esto",
        "ayudame",
        "ok",
        "vale",
        "perfecto",
        "entendido",
        "gracias!",
        "me ayudas?",
    ):
        assert heuristic_is_chat(text) is True, text


def test_long_conversational_message_is_still_chat() -> None:
    """El corte por longitud ya no se aplica antes de mirar las pistas."""
    largo = "Te pego un texto para que me des tu opinión: " + "bla " * 60 + "¿qué opinas?"
    assert len(largo) > 160
    assert heuristic_is_chat(largo) is True
    # …pero un mensaje largo sin ninguna señal sigue siendo tarea
    assert heuristic_is_chat("x" * 200) is False


def test_diagnostic_question_about_the_project_is_a_task() -> None:
    """"¿por qué falla mi script?" necesita mirar el proyecto, no charlar."""
    assert has_task_hint("¿por qué falla mi script?") is True
    assert heuristic_is_chat("¿por qué falla mi script?") is False
    assert heuristic_is_chat("el test de store está fallando") is False
    # el mismo verbo en una pregunta de conocimiento general sigue siendo chat
    assert heuristic_is_chat("¿qué es un script en python?") is True
    assert heuristic_is_chat("explícame la diferencia entre error y excepción") is True


def test_context_detected_by_file_extension() -> None:
    assert has_project_context("revisa main.py") is True
    assert has_task_hint("arregla el fallo en main.py") is True
    assert heuristic_is_chat("arregla el fallo en main.py") is False


def test_english_questions_are_classified() -> None:
    assert heuristic_is_chat("what can you do?") is True
    assert heuristic_is_chat("help me with this") is True
    assert heuristic_is_chat("what is a closure?") is True
    assert heuristic_is_chat("create a file with hello") is False
    assert heuristic_is_chat("fix the failing test in store.py") is False


def test_is_chat_goal_trusts_heuristic_without_llm(tmp_path: Path):
    """Chat claro (heurística) → NO consulta al LLM (modelos pequeños no fiables)."""

    class FakeChatLLM(FakeLLM):
        async def complete(self, request):
            from satellite.core.messages import LLMResponse

            self.requests.append(request)
            return LLMResponse(content="task")  # aunque el LLM diga task, la heurística manda

    provider = FakeChatLLM()
    task = make_task("¿qué es el infinitivo?", tmp_path)
    assert is_chat_goal(task, provider) is True
    # no se llamó al LLM (heurística decidió sola)
    assert len(provider.requests) == 0


def test_is_chat_goal_ambiguous_uses_llm(tmp_path: Path) -> None:
    """Consulta corta sin pistas claras (ni chat ni tarea) → consulta al LLM."""
    provider = FakeLLM(script=[{"content": "chat"}])
    # "clima de bogota" no tiene pistas de chat ni de tarea, no termina en ? → LLM decide
    task = make_task("clima de bogota", tmp_path)
    assert is_chat_goal(task, provider) is True
    assert len(provider.requests) == 1


def test_is_chat_goal_task_hint_false_even_with_provider(tmp_path: Path) -> None:
    provider = FakeLLM(script=[{"content": "chat"}])
    task = make_task("crea un archivo", tmp_path)
    assert is_chat_goal(task, provider) is False
    # no debe consultar al LLM (la pista de tarea es determinante)
    assert len(provider.requests) == 0


def test_is_chat_goal_without_provider_falls_to_heuristic(tmp_path: Path) -> None:
    task = make_task("hola, ¿qué tal?", tmp_path)
    assert is_chat_goal(task, None) is True
    task2 = make_task("escribe una función en python", tmp_path)
    assert is_chat_goal(task2, None) is False
