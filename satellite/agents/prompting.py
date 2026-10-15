"""Construcción del prefijo de mensajes para los agentes base y meta-prompts.

Orden fijo (cacheable): **tools → system → catálogo (USER) → dinámico (USER)**.
El catálogo NUNCA va en un bloque `system`: va como primer bloque USER dentro de
`<data>`, con su propio breakpoint de cache.
"""

from __future__ import annotations

from satellite.agents.prompts import render_template
from satellite.agents.runtime import build_system_prompt
from satellite.core.messages import Message, Role
from satellite.retrieval.sanitize import neutralize_delimiters, wrap_data

CATALOG_SOURCE = "catalogo"


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
) -> tuple[tuple[Message, ...], int]:
    """Devuelve ``(mensajes, stable_user_count)`` con el orden canónico.

    ``stable_user_count`` es 1 cuando hay bloque de catálogo (prefijo estable) y
    0 cuando no lo hay.
    """
    system = build_system_prompt(
        system_prompt, output_schema=output_schema, tools_text=tools_text
    )
    messages: list[Message] = [Message(role=Role.SYSTEM, content=system)]
    stable = 0
    if catalog:
        messages.append(
            Message(role=Role.USER, content=wrap_data(CATALOG_SOURCE, catalog))
        )
        stable = 1
    dynamic = render_template(
        user_template,
        task=neutralize_delimiters(task),
        context=neutralize_delimiters(context),
        feedback=neutralize_delimiters(feedback),
    )
    messages.append(Message(role=Role.USER, content=dynamic))
    return tuple(messages), stable
