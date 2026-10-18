"""Selección de backend por plataforma (F6).

Windows (UIA), macOS (AX) y Linux (AT-SPI) son backends **opcionales**: la
selección no importa la dependencia de plataforma hasta que se usa (salvo el
constructor, que tampoco importa). Plataformas desconocidas → ``UnsupportedPlatform``.
"""

from __future__ import annotations

import sys

from .base import DesktopBackend
from .errors import UnsupportedPlatform


def backend_for_platform(platform: str | None = None) -> DesktopBackend:
    """Devuelve el backend para ``platform`` (por defecto, el actual)."""
    current = platform or sys.platform
    if current.startswith("win"):
        from .windows import WindowsUiaBackend

        return WindowsUiaBackend()
    if current.startswith("darwin"):
        from .macos import MacosAxBackend

        return MacosAxBackend()
    if current.startswith("linux"):
        from .linux import LinuxAtspiBackend

        return LinuxAtspiBackend()
    raise UnsupportedPlatform(
        f"plataforma no soportada para control de escritorio: {current}"
    )
