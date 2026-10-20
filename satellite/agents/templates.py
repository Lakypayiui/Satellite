"""Plantillas de agentes estáticas (F5).

Una plantilla es un ``ProposedAgent`` **determinista** (sin LLM): prompt estático,
herramientas y ``output_schema`` exactos. Se materializa por el mismo camino que
una propuesta (validación → aprobación → registro/persistencia), así que conserva
las garantías de F4.

``desktop_operator`` ya no es una plantilla: es un **agente base** que viaja en
``satellite/agents/specs`` y sólo se expone en superficies con humano. El registro
queda para plantillas futuras.
"""

from __future__ import annotations

from typing import Callable

from satellite.core.proposal import ProposedAgent

_TEMPLATES: dict[str, Callable[[], ProposedAgent]] = {}


def template_ids() -> tuple[str, ...]:
    """Ids de plantillas disponibles."""
    return tuple(sorted(_TEMPLATES))


def has_template(template_id: str) -> bool:
    """True si ``template_id`` es una plantilla conocida."""
    return str(template_id or "").strip().lower() in _TEMPLATES


def get_template(template_id: str) -> ProposedAgent | None:
    """Instancia de la plantilla (o ``None`` si no existe)."""
    factory = _TEMPLATES.get(str(template_id or "").strip().lower())
    return factory() if factory is not None else None
