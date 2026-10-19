"""Respuestas locales sobre el propio sistema (sin LLM y sin grafo).

Preguntas como *"¿qué agentes tienes?"*, *"¿qué skills?"*, *"¿qué herramientas?"*
o *"¿qué modelo eres?"* se contestan desde los registros del runtime: es exacto,
instantáneo, gratis y no construye ningún grafo. El **texto de respuesta** sigue el
idioma del usuario (ids, nombres y rutas quedan literales); las etiquetas de UI/CLI no
se tocan.
"""

from __future__ import annotations

import re

from satellite.core.language import detect_user_language

AGENTS = "agents"
SKILLS = "skills"
TOOLS = "tools"
MODEL = "model"

# Idioma de producto por defecto cuando no hay señal suficiente.
_DEFAULT_LANGUAGE = "es"

# Marcadores de pregunta sobre el propio sistema. Ojo con el "que" español: casi
# siempre es pronombre relativo ("el agente que audita"), así que NO basta para que
# una frase sea una pregunta; sólo cuenta al abrir la frase ("¿qué agentes…?").
_WH_START_RE = re.compile(
    r"^[¿¡]?\s*(qu[eé]|cu[aá]l(es)?|cu[aá]nt[oa]s?|what|which|how many)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"\b(cu[aá]l(es)?|cu[aá]nt[oa]s?|lista|listar|muestra|mu[eé]strame|dime|ver|"
    r"what|which|how many|list|show)\b",
    re.IGNORECASE,
)

# Órdenes de trabajo (crear/hacer algo): mencionan agentes o skills, pero NO son
# preguntas del catálogo; van al planificador (p. ej. "crea el agente que audita").
_ACTION_RE = re.compile(
    r"\b(crea|crear|genera|generar|implementa|implementar|construye|construir|"
    r"a[ñn]ade|anade|agrega|escribe|escribir|modifica|modificar|actualiza|actualizar|"
    r"arregla|arreglar|corrige|corregir|ejecuta|ejecutar|instala|instalar|"
    r"create|write|add|build|implement|generate|modify|update|fix|run|install)\b",
    re.IGNORECASE,
)

_INTENT_RE: tuple[tuple[str, re.Pattern], ...] = (
    (AGENTS, re.compile(r"\b(agentes?|agents?|cat[aá]logo)\b", re.IGNORECASE)),
    (SKILLS, re.compile(r"\b(skills?|habilidades?)\b", re.IGNORECASE)),
    (TOOLS, re.compile(r"\b(herramientas?|tools?|primitivas?)\b", re.IGNORECASE)),
    (MODEL, re.compile(r"\b(modelos?|model|proveedor|provider|llm)\b", re.IGNORECASE)),
)


def catalog_intent(goal: str) -> str:
    """Intención de catálogo detectada (``""`` si no es una pregunta del sistema).

    Una pregunta de catálogo es una **pregunta** ("¿qué agentes tienes?", "muéstrame
    las skills"). Una orden de trabajo que sólo menciona agentes/skills ("crea el
    agente que audita", "la recomendada pero crea el agente que audita") NO lo es: va
    al planificador, no se contesta con el catálogo.
    """
    text = str(goal or "").strip().lower()
    if not text:
        return ""
    interrogative = (
        text.endswith("?")
        or bool(_WH_START_RE.search(text))
        or bool(_QUESTION_RE.search(text))
    )
    if not interrogative:
        return ""
    # Imperativo con acción ("dime cómo crear el agente…"): es una tarea, no una
    # consulta. Sólo se permite si es una pregunta de verdad (termina en "?" o abre
    # con una palabra interrogativa: qué/cuál/cuántos/what/which).
    if _ACTION_RE.search(text) and not (
        text.endswith("?") or _WH_START_RE.search(text)
    ):
        return ""
    for name, pattern in _INTENT_RE:
        if pattern.search(text):
            return name
    return ""


def answer_catalog_question(goal: str, services, history=None) -> str | None:
    """Respuesta local desde el catálogo real, o ``None`` si la pregunta no aplica.

    El texto sale en el idioma del usuario: el mensaje actual manda y, si es ambiguo,
    el turno de usuario más reciente (``history``). Sin señal → idioma de producto.
    """
    intent = catalog_intent(goal)
    if not intent:
        return None
    language = detect_user_language(goal, history) or _DEFAULT_LANGUAGE
    if intent == AGENTS:
        return _agents(services, language)
    if intent == SKILLS:
        return _skills(services, language)
    if intent == TOOLS:
        return _tools(services, language)
    return _model(services, language)


def _pick(language: str, es: str, en: str) -> str:
    """Texto de respuesta según el idioma detectado (``en`` sólo si es inglés)."""
    return en if language == "en" else es


# ---------------------------------------------------------------- respuestas


def _agents(services, language: str) -> str:
    registry = getattr(services, "registry", None)
    specs = list(registry.all()) if registry is not None else []
    if not specs:
        return _pick(
            language,
            "Ahora mismo no tengo ningún agente cargado.",
            "I have no agents loaded right now.",
        )
    header = _pick(
        language,
        f"Tengo {len(specs)} agentes disponibles:",
        f"I have {len(specs)} agents available:",
    )
    label = _pick(language, "capacidades", "capabilities")
    lines = [header]
    for spec in sorted(specs, key=lambda item: item.id):
        capabilities = ", ".join(spec.capabilities) or "-"
        description = spec.description or spec.name
        lines.append(f"- {spec.id}: {description} ({label}: {capabilities})")
    lines.append(
        _pick(
            language,
            "Las capacidades del kernel (run_python, run_argv, read_file…) son "
            "herramientas, no agentes ni skills.",
            "The kernel capabilities (run_python, run_argv, read_file…) are tools, "
            "not agents or skills.",
        )
    )
    return "\n".join(lines)


def _skills(services, language: str) -> str:
    registry = getattr(services, "skills", None)
    if registry is None:
        return _pick(
            language,
            "En este runtime no tengo acceso al catálogo de skills.",
            "In this runtime I have no access to the skill catalog.",
        )
    skills = list(registry.all())
    if not skills:
        return _pick(
            language,
            "Ahora mismo no hay skills guardadas. Las primitivas del runtime "
            "(run_python, run_argv, read_file, write_file, list_dir, search_files) "
            "no son skills: son herramientas del kernel.",
            "There are no saved skills right now. The runtime primitives "
            "(run_python, run_argv, read_file, write_file, list_dir, search_files) "
            "are not skills: they are kernel tools.",
        )
    header = _pick(
        language,
        f"Tengo {len(skills)} skills guardadas:",
        f"I have {len(skills)} saved skills:",
    )
    determinista = _pick(language, "(determinista)", "(deterministic)")
    lines = [header]
    for manifest in sorted(skills, key=lambda item: item.id):
        detail = f" {determinista}" if getattr(manifest, "deterministic", False) else ""
        lines.append(f"- {manifest.id}: {manifest.description}{detail}")
    return "\n".join(lines)


def _tools(services, language: str) -> str:
    registry = getattr(services, "tools", None)
    tools = list(registry.all()) if registry is not None else []
    if not tools:
        return _pick(
            language,
            "Ahora mismo no tengo herramientas registradas.",
            "I have no tools registered right now.",
        )
    header = _pick(
        language,
        f"Tengo {len(tools)} herramientas:",
        f"I have {len(tools)} tools:",
    )
    lines = [header]
    for tool in sorted(tools, key=lambda item: item.id):
        lines.append(f"- {tool.id}: {tool.description}")
    lines.append(
        _pick(
            language,
            "Todas son del kernel: no son agentes ni skills, y sus ids están "
            "reservados (una skill no puede suplantarlas).",
            "They are all kernel tools: not agents or skills, and their ids are "
            "reserved (a skill cannot impersonate them).",
        )
    )
    return "\n".join(lines)


def _model(services, language: str) -> str:
    provider = getattr(services, "provider", None)
    if provider is None:
        return _pick(
            language,
            "No hay proveedor LLM configurado en este runtime.",
            "No LLM provider is configured in this runtime.",
        )
    current = provider
    while current is not None and not getattr(current, "model", ""):
        current = getattr(current, "inner", None)
    model = getattr(current, "model", "") if current is not None else ""
    name = getattr(provider, "provider_name", "") or type(provider).__name__
    if not model:
        return _pick(
            language,
            f"Uso el proveedor '{name}' (no declara el nombre del modelo).",
            f"I use the provider '{name}' (it does not declare a model name).",
        )
    return _pick(
        language,
        f"Uso el modelo '{model}' a través del proveedor '{name}'.",
        f"I use the model '{model}' through the provider '{name}'.",
    )


__all__ = ["AGENTS", "MODEL", "SKILLS", "TOOLS", "answer_catalog_question", "catalog_intent"]
