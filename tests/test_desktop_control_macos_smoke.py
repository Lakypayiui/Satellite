"""Smoke real macOS AX (opt-in).

Se salta salvo que ``SATELLITE_DESKTOP_CONTROL_MACOS_SMOKE=1``, estemos en macOS,
pyobjc esté instalado y el proceso tenga permiso de Accesibilidad. Crea su propia
ventana Tk y sólo la observa.
"""

import asyncio
import os
import sys

import pytest

from satellite.desktop_control.errors import EnvironmentUnavailable, MissingDependency
from satellite.desktop_control.macos import MacosAxBackend, ax_available

_HABILITADO = os.environ.get("SATELLITE_DESKTOP_CONTROL_MACOS_SMOKE") == "1"
_TITULO = "Satellite AX Smoke"

pytestmark = pytest.mark.skipif(
    not (_HABILITADO and sys.platform.startswith("darwin") and ax_available()),
    reason=(
        "smoke macOS AX deshabilitado: activa SATELLITE_DESKTOP_CONTROL_MACOS_SMOKE=1 "
        "en macOS con el extra desktop-control-macos y permiso de Accesibilidad"
    ),
)


def run(coro):
    return asyncio.run(coro)


def test_observe_own_window_read_only() -> None:
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except Exception as exc:  # pragma: no cover - depende de la sesión gráfica
        pytest.skip(f"sin GUI disponible: {exc}")
    try:
        root.title(_TITULO)
        tkinter.Button(root, text="Guardar").pack()
        root.update()

        backend = MacosAxBackend()
        try:
            windows = run(backend.list_windows())
        except (MissingDependency, EnvironmentUnavailable) as exc:
            pytest.skip(str(exc))
        target = next((w for w in windows if _TITULO in w.title), None)
        if target is None:
            pytest.skip("la ventana propia no aparece en CGWindowList")

        snapshot = run(backend.observe(target))
        assert snapshot.snapshot_id
        assert snapshot.quality in ("good", "poor")
    finally:
        root.destroy()
