"""Clasificador de consultas: ¿conversacional (chat-directo) o tarea de proyecto?

Heurística rápida + confirmación LLM ligera. Diseñado para que una pregunta
básica NO genere grafos ni llame tools: si es conversacional, se responde
directo con el LLM.
"""

from __future__ import annotations

import re

from satellite.core.messages import LLMRequest, Message, Role
from satellite.core.task import Task
from satellite.providers.base import LLMProvider

# Palabras que indican claramente UNA TAREA de proyecto (código/archivos/tools)
_TASK_HINTS_RE = re.compile(
    r"\b(crea|crear|escribe|escribir|arregla|arreglar|corrige|corregir|actualiza|actualizar|"
    r"modifica|modificar|genera|generar|implementa|implementar|haz|hacer|construye|construir|"
    r"instala|instalar|ejecuta|ejecutar|compila|compilar|refactoriza|refactorizar|"
    r"script|clase|función|funcion|archivo|código|codigo|repo|proyecto|"
    r"html|css|python|java|javascript|typescript|cpp|go|rust|sql|git|pytest|test)\b",
    re.IGNORECASE,
)

# Palabras típicas de conversación simple
_CHAT_HINTS_RE = re.compile(
    r"\b(hola|buenas|gracias|qué es|que es|quien|quién|cuál es|cual es|cuáles son|cuales son|"
    r"explícame|explicame|dime|resume|explica|cómo estás|como estas|"
    r"cuéntame|cuentame|significa|diferencia|ventajas|desventajas|opinión|opina|"
    r"estás|puedes ayudarme|me ayudas|consejo|sugerencia)\b",
    re.IGNORECASE,
)

# Longitud máxima para considerar "posible conversación corta"
_MAX_LEN = 160


def heuristic_is_chat(goal: str) -> bool:
    """Regla rápida: ¿parece conversación simple (sin pista de tarea de código)?"""
    text = goal.strip().lower()
    if not text:
        return False
    # Muy largo → probablemente tarea
    if len(goal) > _MAX_LEN:
        return False
    # Si contiene pistas de tarea de proyecto → NO es chat
    if _TASK_HINTS_RE.search(text):
        return False
    # Si parece pregunta casual o saludo → ES chat
    if _CHAT_HINTS_RE.search(text):
        return True
    # Pregunta corta sin verbos de acción de código → probablemente chat
    return len(goal) <= 60 and goal.endswith("?")


def is_chat_goal(task: Task, provider: LLMProvider | None) -> bool:
    """Clasifica el goal: True = conversacional (responder directo sin tools).

    Híbrido síncrono: heurística rápida. Solo consulta al LLM en casos
    AMBIGUOS (pregunta corta sin pista clara de chat ni de tarea). Si la
    heurística identifica chat claro (saludo, "qué es", "cómo estás") no
    gasta una llamada: los modelos pequeños no clasifican bien y la heurística
    ya es sólida para eso.
    """
    # Heurística contundente: pistas de tarea presentes → NO chat (sin LLM)
    if _TASK_HINTS_RE.search(task.goal.lower()):
        return False
    heuristic = heuristic_is_chat(task.goal)
    # Chat claro según heurística → confiar en ella (sin llamar al LLM)
    if heuristic:
        return True
    # Dudoso: consulta corta sin pistas claras → consultar al LLM si hay provider
    if len(task.goal) <= 60 and provider is not None:
        return _run_sync(_llm_classify(task.goal, provider))
    return False


async def async_is_chat_goal(task: Task, provider: LLMProvider | None) -> bool:
    """Versión async (usada dentro del orchestrator)."""
    if _TASK_HINTS_RE.search(task.goal.lower()):
        return False
    heuristic = heuristic_is_chat(task.goal)
    if heuristic:
        return True
    if len(task.goal) <= 60 and provider is not None:
        return await _llm_classify(task.goal, provider)
    return False


async def _llm_classify(goal: str, provider: LLMProvider) -> bool:
    """Pregunta al LLM (una llamada corta) si el goal es conversacional."""
    prompt = (
        "Clasifica la siguiente consulta del usuario:\n"
        f"\"{goal}\"\n\n"
        "Responde SOLO con 'chat' si es una pregunta conversacional simple "
        "(saludo, definición, opinión, curiosidad, explicación general) que NO "
        "requiere modificar archivos, ejecutar comandos, crear código ni "
        "analizar el proyecto.\n"
        "Responde SOLO con 'task' si es una tarea que requiere usar el "
        "proyecto, archivos, código, herramientas o ejecución.\n"
        "Responde únicamente: chat | task"
    )
    request = LLMRequest(
        messages=(
            Message(role=Role.SYSTEM, content="Eres un clasificador binario. Responde solo 'chat' o 'task'."),
            Message(role=Role.USER, content=prompt),
        ),
        temperature=0.0,
        max_tokens=8,
    )
    try:
        response = await provider.complete(request)
        answer = (response.content or "").strip().lower()
        return answer.startswith("chat")
    except Exception:
        # Si el LLM falla, caemos a la heurística (no bloquear la consulta)
        return heuristic_is_chat(goal)


def _run_sync(coro):
    import asyncio

    return asyncio.run(coro)
