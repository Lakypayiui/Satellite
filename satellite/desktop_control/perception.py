"""Percepción degradada (F7): **interfaces**, sin OCR ni visión implementados.

Cuando un snapshot sale ``quality="poor"`` la tool indica opciones explícitas:
- **OCR local con cajas**: interfaz definida (``OcrFallback`` + ``OcrResult``/
  ``OcrBox``), implementación **ausente/opcional**. Por defecto ``available=False``.
- **Escalar a un proveedor con visión**: sólo interfaz (``VisionFallback``); se marca
  disponible únicamente si el host inyecta uno configurado. No se implementa visión.
- **Pedir ayuda al usuario**: siempre disponible.

Nada de esto ejecuta percepción ni cambia políticas/permisos: son datos para que el
agente (o el humano) elija.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

OCR_INTERFACE = "satellite.desktop_control.perception.OcrFallback"
VISION_INTERFACE = "satellite.desktop_control.perception.VisionFallback"


@dataclass(frozen=True)
class OcrBox:
    """Caja de un OCR local (coordenadas relativas a la ventana)."""

    x: int
    y: int
    width: int
    height: int
    text: str = ""
    confidence: float | None = None

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "text": self.text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class OcrResult:
    """Texto reconocido y sus cajas (interfaz; no se produce en F7)."""

    text: str
    boxes: tuple[OcrBox, ...] = ()

    def to_dict(self) -> dict:
        return {"text": self.text, "boxes": [box.to_dict() for box in self.boxes]}


@runtime_checkable
class OcrFallback(Protocol):
    """Interfaz local de OCR con cajas; la implementación real es opcional."""

    @property
    def available(self) -> bool: ...

    def recognize(self, *, snapshot: object) -> OcrResult: ...


class UnavailableOcrFallback:
    """Implementación por defecto: la interfaz existe, el OCR no está implementado."""

    @property
    def available(self) -> bool:
        return False

    def recognize(self, *, snapshot: object) -> OcrResult:
        raise NotImplementedError(
            "OCR local no implementado (interfaz opcional de F7)"
        )


@runtime_checkable
class VisionFallback(Protocol):
    """Interfaz de escalado a un proveedor con visión (sólo interfaz)."""

    @property
    def configured(self) -> bool: ...

    async def escalate(self, *, snapshot: object) -> str: ...


class UnavailableVisionFallback:
    """Implementación por defecto: no hay proveedor con visión configurado."""

    provider = ""
    model = ""

    def __init__(self, *, reason: str = "") -> None:
        self.reason = reason or "no hay proveedor de visión configurado"

    @property
    def configured(self) -> bool:
        return False

    async def escalate(self, *, snapshot: object) -> str:
        raise NotImplementedError(
            "escalado a proveedor con visión no implementado (sólo interfaz F7)"
        )


class ConfiguredVisionFallback:
    """Fallback **seleccionado/ configurado**, sólo interfaz (no implementa visión).

    Marca disponibilidad de la interfaz y expone proveedor/modelo para que
    ``desktop_observe`` lo reporte. ``escalate`` sigue sin implementación: no se
    envían imágenes ni se llama al proveedor.
    """

    implemented = False

    def __init__(self, *, provider: str, model: str, reason: str = "") -> None:
        self.provider = str(provider or "")
        self.model = str(model or "")
        self.reason = str(reason or "")

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.model)

    async def escalate(self, *, snapshot: object) -> str:
        raise NotImplementedError(
            "envío real de imágenes/visión no implementado (sólo interfaz F7)"
        )


def vision_status(vision: VisionFallback | None = None) -> dict:
    """Estado de la interfaz de visión (sin afirmar que la visión funcione)."""
    engine: Any = vision if vision is not None else UnavailableVisionFallback()
    configured = bool(getattr(engine, "configured", False))
    provider = str(getattr(engine, "provider", "") or "")
    model = str(getattr(engine, "model", "") or "")
    reason = str(getattr(engine, "reason", "") or "")
    if not configured and not reason:
        reason = "no hay proveedor de visión configurado"
    if configured:
        note = (
            "proveedor/modelo seleccionado; visión real NO implementada "
            "(F7: sólo interfaz, no se envían imágenes)"
        )
    else:
        note = (
            "no hay proveedor de visión configurado; visión real NO implementada "
            "(F7: sólo interfaz)"
        )
    return {
        "configured": configured,
        "available": configured,
        "implemented": False,
        "provider": provider,
        "model": model,
        "reason": reason,
        "interface": VISION_INTERFACE,
        "note": note,
    }


def degraded_options(
    *,
    ocr: OcrFallback | None = None,
    vision: VisionFallback | None = None,
) -> dict:
    """Opciones explícitas para un snapshot ``poor`` (sin ejecutar percepción)."""
    ocr_engine: Any = ocr if ocr is not None else UnavailableOcrFallback()
    ocr_ok = bool(getattr(ocr_engine, "available", False))
    return {
        "ocr": {
            "available": ocr_ok,
            "interface": OCR_INTERFACE,
            "boxes": True,
            "note": (
                "OCR local opcional con cajas (interfaz); no implementado en esta fase"
                if not ocr_ok
                else "OCR local inyectado por el host (interfaz)"
            ),
        },
        "vision": vision_status(vision),
        "ask_user": {
            "available": True,
            "message": (
                "La observación es poor: pide al usuario que indique el control "
                "o aporte contexto."
            ),
        },
    }
