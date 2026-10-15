"""Respuestas locales sobre el propio sistema (sin LLM y sin grafo).

Preguntas como *"¿qué agentes tienes?"*, *"¿qué skills?"*, *"¿qué herramientas?"*
o *"¿qué modelo eres?"* se contestan desde los registros del runtime: es exacto,
instantáneo, gratis y no construye ningún grafo.
"""

from __future__ import annotations

import re

AGENTS = "agents"
SKILLS = "skills"
TOOLS = "tools"
MODEL = "model"

_QUESTION_RE = re.compile(
    r"\b(qu[eé]|cu[aá]l(es)?|lista|listar|muestra|mu[eé]strame|dime|ver)\b", re.IGNORECASE
)

_INTENT_RE: tuple[tuple[str, re.Pattern], ...] = (
    (AGENTS, re.compile(r"\b(agentes?|agents?|cat[aá]logo)\b", re.IGNORECASE)),
    (SKILLS, re.compile(r"\b(skills?|habilidades?)\b", re.IGNORECASE)),
    (TOOLS, re.compile(r"\b(herramientas?|tools?|primitivas?)\b", re.IGNORECASE)),
    (MODEL, re.compile(r"\b(modelos?|model|proveedor|provider|llm)\b", re.IGNORECASE)),
)


def catalog_intent(goal: str) -> str:
    """Intención de catálogo detectada (``""`` si no es una pregunta del sistema)."""
    text = str(goal or "").strip().lower()
    if not text:
        return ""
    if not (text.endswith("?") or _QUESTION_RE.search(text)):
        return ""
    for name, pattern in _INTENT_RE:
        if pattern.search(text):
            return name
    return ""


def answer_catalog_question(goal: str, services) -> str | None:
    """Respuesta local desde el catálogo real, o ``None`` si la pregunta no aplica."""
    intent = catalog_intent(goal)
    if not intent:
        return None
    if intent == AGENTS:
        return _agents(services)
    if intent == SKILLS:
        return _skills(services)
    if intent == TOOLS:
        return _tools(services)
    return _model(services)


# ---------------------------------------------------------------- respuestas


def _agents(services) -> str:
    registry = getattr(services, "registry", None)
    specs = list(registry.all()) if registry is not None else []
    if not specs:
        return "Ahora mismo no tengo ningún agente cargado."
    lines = [f"Tengo {len(specs)} agentes disponibles:"]
    for spec in sorted(specs, key=lambda item: item.id):
        capabilities = ", ".join(spec.capabilities) or "-"
        description = spec.description or spec.name
        lines.append(f"- {spec.id}: {description} (capacidades: {capabilities})")
    lines.append(
        "Las capacidades del kernel (run_python, run_shell, read_file…) son "
        "herramientas, no agentes ni skills."
    )
    return "\n".join(lines)


def _skills(services) -> str:
    registry = getattr(services, "skills", None)
    if registry is None:
        return "En este runtime no tengo acceso al catálogo de skills."
    skills = list(registry.all())
    if not skills:
        return (
            "Ahora mismo no hay skills guardadas. Las primitivas del runtime "
            "(run_python, run_shell, read_file, write_file, list_dir, search_files) "
            "no son skills: son herramientas del kernel."
        )
    lines = [f"Tengo {len(skills)} skills guardadas:"]
    for manifest in sorted(skills, key=lambda item: item.id):
        detail = " (determinista)" if getattr(manifest, "deterministic", False) else ""
        lines.append(f"- {manifest.id}: {manifest.description}{detail}")
    return "\n".join(lines)


def _tools(services) -> str:
    from satellite.kernel.manifest import is_reserved

    registry = getattr(services, "tools", None)
    tools = list(registry.all()) if registry is not None else []
    if not tools:
        return "Ahora mismo no tengo herramientas registradas."
    primitives = [tool for tool in tools if is_reserved(tool.id)]
    lines = [f"Tengo {len(tools)} herramientas ({len(primitives)} son primitivas del kernel):"]
    for tool in sorted(tools, key=lambda item: item.id):
        mark = " [primitiva]" if is_reserved(tool.id) else ""
        lines.append(f"- {tool.id}{mark}: {tool.description}")
    return "\n".join(lines)


def _model(services) -> str:
    provider = getattr(services, "provider", None)
    if provider is None:
        return "No hay proveedor LLM configurado en este runtime."
    current = provider
    while current is not None and not getattr(current, "model", ""):
        current = getattr(current, "inner", None)
    model = getattr(current, "model", "") if current is not None else ""
    name = getattr(provider, "provider_name", "") or type(provider).__name__
    if not model:
        return f"Uso el proveedor '{name}' (no declara el nombre del modelo)."
    return f"Uso el modelo '{model}' a través del proveedor '{name}'."


__all__ = ["AGENTS", "MODEL", "SKILLS", "TOOLS", "answer_catalog_question", "catalog_intent"]
