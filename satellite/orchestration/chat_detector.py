"""Clasificador de consultas: ¿conversacional (chat-directo) o tarea de proyecto?

Heurística determinista + confirmación LLM **solo** para casos ambiguos. Objetivo:
una pregunta simple NO genera grafos ni llama tools, y una petición de trabajo
real sí se planifica.

Orden de decisión:
1. Preguntas sobre el propio sistema ("¿qué agentes tienes?") → conversación.
2. **Acción o diagnóstico CON contexto real de proyecto** → tarea.
3. Pregunta por el proyecto en conjunto (overview/README) → conversación.
4. Preguntas de conocimiento general ("¿qué es X?") → conversación.
5. Cortesía y relleno ("hola", "gracias", "ok", "ayúdame") → conversación.
6. Mensaje largo sin ninguna señal → tarea.
7. Pregunta corta con "?" → conversación.

Los patrones cubren español e inglés.
"""

from __future__ import annotations

import re

from satellite.core.messages import LLMRequest, Message, Role
from satellite.core.task import Task
from satellite.providers.base import LLMProvider

# Verbos de acción sobre el proyecto (por sí solos NO bastan: exigen contexto).
_ACTION_RE = re.compile(
    r"\b(crea|crear|escribe|escribir|arregla|arreglar|corrige|corregir|actualiza|actualizar|"
    r"modifica|modificar|genera|generar|implementa|implementar|construye|construir|"
    r"instala|instalar|ejecuta|ejecutar|compila|compilar|refactoriza|refactorizar|"
    r"borra|elimina|renombra|mueve|añade|anade|agrega|documenta|revisa|"
    r"create|write|add|remove|delete|update|fix|refactor|implement|build|generate|"
    r"rename|move|install|run|execute|compile|review|document)\b",
    re.IGNORECASE,
)

# Señales de que algo NO funciona en el proyecto (preguntar por eso es tarea).
_DIAGNOSTIC_RE = re.compile(
    r"\b(falla|fallo|fallando|error|errores|no funciona|no anda|rompe|roto|"
    r"excepci[oó]n|traceback|depura|depurar|debug|se cae|crash|bug|"
    r"fails?|failing|broken|exception|breaks?)\b",
    re.IGNORECASE,
)

# Contexto real de proyecto (convierte una acción o un diagnóstico en tarea).
_CONTEXT_RE = re.compile(
    r"\b(archivo|archivos|fichero|ficheros|carpeta|carpetas|directorio|"
    r"c[oó]digo|codigo|script|clase|funci[oó]n|funcion|m[oó]dulo|modulo|repo|"
    r"repositorio|proyecto|html|css|json|yaml|python|java|javascript|typescript|"
    r"cpp|go|rust|sql|bash|powershell|git|commit|pytest|test|tests|readme|log|logs|"
    r"agente|agentes|skill|skills|herramienta|herramientas|tool|tools|"
    r"file|files|code|function|class|module|folder|directory|project|test|tests)\b",
    re.IGNORECASE,
)

# Un path con extensión cuenta como contexto aunque no aparezca la palabra.
_FILE_RE = re.compile(r"\b[\w./\\-]+\.[a-z0-9]{1,5}\b", re.IGNORECASE)

# Preguntas sobre el propio sistema: conversación (y se responden localmente).
_META_CHAT_RE = re.compile(
    r"\b(qu[eé] puedes hacer|que puedes hacer|qu[eé] sabes hacer|qu[eé] eres|qui[eé]n eres|"
    r"qu[eé] modelos?|qu[eé] agente?s?|qu[eé] skills?|qu[eé] habilidade?s?|"
    r"qu[eé] herramientas?|qu[eé] tools?|qu[eé] capacidades|qu[eé] funciones|"
    r"c[oó]mo funcionas|para qu[eé] sirves|qu[eé] versi[oó]n|"
    r"what can you do|what are you|who are you|what agents|which agents|"
    r"what tools|what skills|what model|how do you work)\b",
    re.IGNORECASE,
)

# Conocimiento general: no requiere tocar el proyecto.
_KNOWLEDGE_CHAT_RE = re.compile(
    r"\b(qu[eé] es|que es|qu[eé] significa|significa|expl[ií]came|explica|explicar|"
    r"diferencia entre|comparaci[oó]n|ventajas|desventajas|opini[oó]n|opinas|opina|"
    r"cu[aá]nto es|ejemplo de|resumen de la teor[ií]a|qu[eé] son|"
    r"what is|what are|how does|difference between|explain|your opinion)\b",
    re.IGNORECASE,
)

# Cortesía, relleno y peticiones de ayuda sin objeto concreto.
_CHAT_HINTS_RE = re.compile(
    r"\b(hola|buenas|buenos d[ií]as|gracias|ok|okey|vale|perfecto|genial|entendido|"
    r"de acuerdo|listo|ayúdame|ayudame|ayudarme|me ayudas|puedes ayudarme|"
    r"consejo|sugerencia|dime algo|cu[eé]ntame algo|"
    r"qu[eé] es|que es|quien|qui[eé]n|cu[aá]l es|cual es|cu[aá]les son|cuales son|"
    r"qu[eé] hay|resume|resumen|est[aá]s|tal|hola de nuevo|"
    r"help me|thanks|thank you|hello|hi|what is)\b",
    re.IGNORECASE,
)

# Pregunta por el PROYECTO en conjunto (overview/README), no por una acción sobre él.
# Combina un sujeto de proyecto con una intención de resumen/explicación. Es
# conversación aunque no lleve "?" ni abra con palabra interrogativa, que era justo
# lo que dejaba "De que trata el proyecto" en manos del planner y del clasificador LLM.
# Variantes naturales ES/EN: "de qué trata el proyecto", "¿De qué trata este proyecto?",
# "de qué va el repo", "en qué consiste el proyecto", "qué hace el proyecto",
# "what is the project about", "what does this repo do".
_PROJECT_SUBJECT_RE = re.compile(
    r"\b(proyecto|project|repo|repositorio|repository|codebase)\b",
    re.IGNORECASE,
)
_PROJECT_OVERVIEW_INTENT_RE = re.compile(
    r"\b(de qu[eé] trata|de que trata|se trata|trata|consiste|de qu[eé] va|"
    r"qu[eé] es|que es|qu[eé] hace|que hace|resumen|resume|describe|"
    r"descripci[oó]n|expl[ií]ca|explica|sobre qu[eé]|cu[eé]ntame|sirve|funciona|"
    r"about|what is|what's|what does|overview|summary|describe|explain|purpose)\b",
    re.IGNORECASE,
)

# Longitud máxima para considerar "posible conversación" si no hay más señales.
_MAX_LEN = 160


def is_project_overview(goal: str) -> bool:
    """True si pregunta de qué trata el proyecto (overview), no una acción sobre él.

    Determinista y barato: exige un sujeto de proyecto **y** una intención de
    resumen/explicación. La acción con contexto de proyecto se filtra antes
    (:func:`has_task_hint`), así que "arregla el proyecto" o "documenta el proyecto"
    siguen siendo tareas.
    """
    text = str(goal or "").strip()
    return bool(
        _PROJECT_SUBJECT_RE.search(text) and _PROJECT_OVERVIEW_INTENT_RE.search(text)
    )


def has_project_context(goal: str) -> bool:
    """True si el mensaje apunta a ficheros, código, tools o agentes del proyecto."""
    text = str(goal or "").strip().lower()
    return bool(_CONTEXT_RE.search(text) or _FILE_RE.search(text))


def has_task_hint(goal: str) -> bool:
    """True si pide una **acción** o reporta un **fallo** con contexto de proyecto."""
    text = str(goal or "").strip().lower()
    if not has_project_context(text):
        return False
    return bool(_ACTION_RE.search(text) or _DIAGNOSTIC_RE.search(text))


def heuristic_is_chat(goal: str) -> bool:
    """Regla rápida: ¿parece conversación (sin pista de trabajo)?"""
    text = str(goal or "").strip().lower()
    if not text:
        return False
    # 1) Preguntas sobre el propio sistema → conversación
    if _META_CHAT_RE.search(text):
        return True
    # 2) Acción o diagnóstico con contexto de proyecto → tarea
    if has_task_hint(text):
        return False
    # 3) Pregunta por el proyecto en conjunto (overview/README) → conversación
    if is_project_overview(text):
        return True
    # 4) Conocimiento general → conversación
    if _KNOWLEDGE_CHAT_RE.search(text):
        return True
    # 5) Cortesía, relleno o "ayúdame" → conversación
    if _CHAT_HINTS_RE.search(text):
        return True
    # 6) Mensaje largo sin ninguna señal → tarea
    if len(text) > _MAX_LEN:
        return False
    # 7) Pregunta corta → conversación
    return len(text) <= 60 and text.endswith("?")


def is_chat_goal(task: Task, provider: LLMProvider | None) -> bool:
    """Clasifica el goal: True = conversacional (responder directo sin tools).

    Híbrido: heurística determinista y, solo en casos AMBIGUOS (mensaje corto sin
    pistas claras de chat ni de tarea), una consulta corta al LLM. Si la
    heurística lo tiene claro no gasta llamada: los modelos pequeños no
    clasifican bien y aquí la regla ya es sólida.
    """
    if has_task_hint(task.goal):
        return False
    if heuristic_is_chat(task.goal):
        return True
    if len(task.goal.strip()) <= 60 and provider is not None:
        return _run_sync(_llm_classify(task.goal, provider))
    return False


async def async_is_chat_goal(task: Task, provider: LLMProvider | None) -> bool:
    """Versión async (usada dentro del orchestrator)."""
    if has_task_hint(task.goal):
        return False
    if heuristic_is_chat(task.goal):
        return True
    if len(task.goal.strip()) <= 60 and provider is not None:
        return await _llm_classify(task.goal, provider)
    return False


async def _llm_classify(goal: str, provider: LLMProvider) -> bool:
    """Pregunta al LLM (una llamada corta) si el goal es conversacional."""
    prompt = (
        "Classify the following user query:\n"
        f"\"{goal}\"\n\n"
        "Reply ONLY with 'chat' if it is a simple conversational question "
        "(greeting, definition, opinion, curiosity, general explanation) that does "
        "NOT require modifying files, running commands, creating code or analyzing "
        "the project.\n"
        "Reply ONLY with 'task' if it is a task that requires using the project, "
        "files, code, tools or execution.\n"
        "Reply with only: chat | task"
    )
    request = LLMRequest(
        messages=(
            Message(role=Role.SYSTEM, content="You are a binary classifier. Reply only 'chat' or 'task'."),
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


async def classify_project_question(
    goal: str,
    provider: LLMProvider,
    *,
    conversation: str = "",
) -> str | None:
    """Clasifica la intención de una consulta del proyecto con el modelo activo.

    ``None`` significa que el proveedor no pudo clasificarla; el llamador puede usar
    una regla local de respaldo en ese caso.
    """
    prompt = (
        "Classify the user's latest request about the active project. Use the complete "
        "request and conversation to infer intent, including natural paraphrases.\n\n"
        "Return exactly one label:\n"
        "structure — the user asks to see or describe project files, folders, or tree.\n"
        "overview — the user asks what the project is for, what it does, or how it works.\n"
        "other — neither of those; includes requests to change, debug, or run the project."
    )
    if conversation.strip():
        prompt += f"\n\nRecent conversation:\n{conversation.strip()}"
    prompt += f"\n\nLatest user request:\n{goal.strip()}"
    request = LLMRequest(
        messages=(
            Message(
                role=Role.SYSTEM,
                content=(
                    "You are a project-intent classifier. Read the user's actual request "
                    "and conversation, then return only structure, overview, or other."
                ),
            ),
            Message(role=Role.USER, content=prompt),
        ),
        temperature=0.0,
        max_tokens=8,
    )
    try:
        response = await provider.complete(request)
    except Exception:  # noqa: BLE001 — el llamador conserva una ruta de respaldo
        return None
    answer = (response.content or "").strip().lower()
    for label in ("structure", "overview", "other"):
        if answer == label or answer.startswith(label + " ") or answer.startswith(label + "\n"):
            return label
    return None


def _run_sync(coro):
    import asyncio

    return asyncio.run(coro)
