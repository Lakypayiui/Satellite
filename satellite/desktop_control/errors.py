"""Errores del subsistema de control de escritorio (no es kernel).

``MissingDependency`` indica exactamente qué extra instalar; ``UnsupportedPlatform``
lo usan plataformas sin backend todavía (macOS/Linux llegarán en F6).
"""

from __future__ import annotations

from satellite.core.errors import SatelliteError


class DesktopControlError(SatelliteError):
    """Base de los errores del subsistema de control de escritorio."""


class MissingDependency(DesktopControlError):
    """Falta una dependencia opcional (extra) para este backend."""

    code = "missing_dependency"

    def __init__(self, message: str, *, extra: str = "") -> None:
        self.extra = extra
        super().__init__(message)


class UnsupportedPlatform(DesktopControlError):
    """No hay backend de control de escritorio para esta plataforma (todavía)."""

    code = "unsupported_platform"


class EnvironmentUnavailable(DesktopControlError):
    """El entorno no ofrece la API/servicio necesario (permiso, bus, typelib)."""

    code = "environment_unavailable"
