"""Selección del proveedor/modelo de visión en la configuración existente (F7).

La selección se guarda en ``config.json`` del proyecto, sección
``desktop_control``::

    "desktop_control": {
      "vision_provider": "<id o nombre de un provider guardado>",
      "vision_model": "<modelo>"
    }

No hay una tienda de proveedores nueva: se resuelve contra ``providers[]`` con las
APIs de ``satellite.config.providers``. El flag de habilitación del control sigue
siendo local del host (``desktop_control.json``); esta sección sólo elige el
fallback de visión. La visión real **no** está implementada: el resultado es un
``ConfiguredVisionFallback`` que sólo reporta interfaz/proveedor/modelo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from satellite.config import providers as provider_config

from .perception import (
    ConfiguredVisionFallback,
    UnavailableVisionFallback,
    VisionFallback,
)

SECTION = "desktop_control"


@dataclass(frozen=True)
class VisionSelection:
    """Resultado de resolver la selección contra los proveedores guardados."""

    configured: bool
    provider: str = ""
    model: str = ""
    reason: str = ""


def project_section(project_root: Path | str) -> dict:
    """Sección ``desktop_control`` de ``config.json`` (``{}`` si falta)."""
    data = provider_config.load_config(project_root)
    raw = data.get(SECTION)
    return raw if isinstance(raw, dict) else {}


def save_selection(project_root: Path | str, *, provider: str, model: str) -> dict:
    """Guarda la selección en ``config.json`` preservando el resto de la config."""
    data = provider_config.load_config(project_root)
    raw = data.get(SECTION)
    section = dict(raw) if isinstance(raw, dict) else {}
    section["vision_provider"] = str(provider or "").strip()
    section["vision_model"] = str(model or "").strip()
    data[SECTION] = section
    provider_config.save_config(project_root, data)
    return section


def resolve_vision_selection(project_root: Path | str) -> VisionSelection:
    """Valida la selección contra ``providers[]`` (sin red ni adivinar capacidades)."""
    raw = project_section(project_root)
    provider = str(raw.get("vision_provider", "")).strip()
    model = str(raw.get("vision_model", "")).strip()
    if not provider:
        return VisionSelection(False, "", model, "sin proveedor de visión configurado")
    saved = provider_config.list_providers(project_root)
    match = next(
        (item for item in saved if item.id == provider or item.name == provider), None
    )
    if match is None:
        return VisionSelection(
            False,
            provider,
            model,
            "el proveedor seleccionado ya no existe en la configuración",
        )
    chosen = model or str(match.model or "").strip()
    if not chosen:
        return VisionSelection(False, match.id, "", "el proveedor no tiene modelo de visión")
    return VisionSelection(True, match.id, chosen, "")


def vision_fallback_for(project_root: Path | str) -> VisionFallback:
    """Fallback de visión a inyectar en ``desktop_observe`` (sólo interfaz)."""
    selection = resolve_vision_selection(project_root)
    if selection.configured:
        return ConfiguredVisionFallback(
            provider=selection.provider, model=selection.model
        )
    return UnavailableVisionFallback(reason=selection.reason)
