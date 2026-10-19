"""Construcción del prefijo de mensajes para los agentes base y meta-prompts.

Orden fijo (cacheable): **tools → system → catálogo (USER) → dinámico (USER)**.
El catálogo NUNCA va en un bloque `system`: va como primer bloque USER dentro de
`<data>`, con su propio breakpoint de cache.
"""

from __future__ import annotations

from satellite.agents.prompts import render_template
from satellite.agents.runtime import build_system_prompt
from satellite.core.language import output_language_hint
from satellite.core.messages import Message, Role
from satellite.retrieval.sanitize import neutralize_delimiters, wrap_data

CATALOG_SOURCE = "catalog"


def build_meta_messages(
    *,
    system_prompt: str,
    user_template: str,
    output_schema: dict | None = None,
    tools_text: str | None = None,
    catalog: str | None = None,
    task: str = "",
    context: str = "",
    feedback: str = "",
    user_language: str | None = None,
) -> tuple[tuple[Message, ...], int]:
    """Devuelve ``(mensajes, stable_user_count)`` con el orden canónico.

    ``stable_user_count`` es 1 cuando hay bloque de catálogo (prefijo estable) y
    0 cuando no lo hay. ``user_language`` (optional) adds the same visible-language
    hint used by executing agents, so user-facing fields of a meta prompt (for
    example a clarification question) are written in the user's language.
    """
    system = build_system_prompt(
        system_prompt, output_schema=output_schema, tools_text=tools_text
    )
    messages: list[Message] = [Message(role=Role.SYSTEM, content=system)]
    stable = 0
    if catalog:
        messages.append(
            Message(
                role=Role.USER,
                # The catalog is generated from validated specs, not retrieved text:
                # truncating it silently hid agents from the orchestrator as it grew.
                content=wrap_data(CATALOG_SOURCE, catalog, max_chars=None),
            )
        )
        stable = 1
    dynamic = render_template(
        user_template,
        task=neutralize_delimiters(task),
        context=neutralize_delimiters(context),
        feedback=neutralize_delimiters(feedback),
    )
    if user_language is not None:
        dynamic = output_language_hint(user_language) + "\n\n" + dynamic
    messages.append(Message(role=Role.USER, content=dynamic))
    return tuple(messages), stable
