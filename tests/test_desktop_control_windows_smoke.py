"""Smoke real Windows del backend UIA (opt-in).

Se salta salvo que ``SATELLITE_DESKTOP_CONTROL_SMOKE=1``, estemos en Windows y
``pywinauto`` esté instalado. Nunca toca apps ajenas: crea su **propia** ventana
Tk (inocua) y sólo la observa (F2 es de sólo lectura).
"""

import asyncio
import os
import sys

import pytest

from satellite.desktop_control.windows import WindowsUiaBackend, pywinauto_available

_TITULO = "Satellite DesktopControl Smoke"
_HABILITADO = os.environ.get("SATELLITE_DESKTOP_CONTROL_SMOKE") == "1"

pytestmark = pytest.mark.skipif(
    not (_HABILITADO and sys.platform.startswith("win") and pywinauto_available()),
    reason=(
        "smoke real deshabilitado: activa SATELLITE_DESKTOP_CONTROL_SMOKE=1 "
        "en Windows con el extra desktop-control instalado"
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
        tkinter.Entry(root, show="*").pack()
        tkinter.Label(root, text="etiqueta").pack()
        root.update()

        backend = WindowsUiaBackend()
        windows = run(backend.list_windows())
        target = next((w for w in windows if _TITULO in w.title), None)
        if target is None:
            pytest.skip("la ventana propia no aparece en el árbol UIA")

        snapshot = run(backend.observe(target))
        assert snapshot.snapshot_id
        assert snapshot.quality in ("good", "poor")
        # Sólo lectura: si el árbol está disponible, el botón propio debe verse.
        assert "Guardar" in snapshot.text or snapshot.quality == "poor"
    finally:
        root.destroy()
