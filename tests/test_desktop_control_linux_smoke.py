"""Smoke real Linux AT-SPI (opt-in).

Se salta salvo que ``SATELLITE_DESKTOP_CONTROL_LINUX_SMOKE=1``, estemos en Linux,
PyGObject/Atspi estén instalados y haya bus de accesibilidad. Crea su propia ventana
Tk y sólo la observa; si AT-SPI no la expone, se salta con un motivo claro.
"""

import asyncio
import os
import sys

import pytest

from satellite.desktop_control.errors import EnvironmentUnavailable, MissingDependency
from satellite.desktop_control.linux import LinuxAtspiBackend, atspi_available

_HABILITADO = os.environ.get("SATELLITE_DESKTOP_CONTROL_LINUX_SMOKE") == "1"
_TITULO = "Satellite AT-SPI Smoke"

pytestmark = pytest.mark.skipif(
    not (_HABILITADO and sys.platform.startswith("linux") and atspi_available()),
    reason=(
        "smoke Linux AT-SPI deshabilitado: activa SATELLITE_DESKTOP_CONTROL_LINUX_SMOKE=1 "
        "en Linux con PyGObject/Atspi y el bus de accesibilidad"
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

        backend = LinuxAtspiBackend()
        try:
            windows = run(backend.list_windows())
        except (MissingDependency, EnvironmentUnavailable) as exc:
            pytest.skip(str(exc))
        target = next((w for w in windows if _TITULO in w.title), None)
        if target is None:
            pytest.skip("la ventana propia no aparece en el árbol AT-SPI")

        snapshot = run(backend.observe(target))
        assert snapshot.snapshot_id
        assert snapshot.quality in ("good", "poor")
    finally:
        root.destroy()
