# -*- mode: python ; coding: utf-8 -*-
"""Ejecutable único ``satellite`` (CLI, TUI, MCP y backend de la app de escritorio).

    pyinstaller packaging/satellite.spec --noconfirm --distpath dist-backend

Genera ``dist-backend/satellite/`` (modo carpeta: arranca mucho más rápido que un único
archivo). La app de Electron lo incluye como recurso (``resources/backend``).

Casi todo el código importa módulos sobre la marcha (tools, proveedores, superficies), así
que se incluyen todos los submódulos de ``satellite`` y de las dependencias que hacen lo
mismo (textual, uvicorn), y los archivos de datos (prompts, specs, helper de Pele, CSS).
"""

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("satellite", filter=lambda name: ".tests" not in name)
    + collect_submodules("textual")
    + collect_submodules("uvicorn")
)
datas = collect_data_files("satellite") + collect_data_files("textual")

# Control del escritorio: backends nativos opcionales, sólo en su sistema.
if sys.platform == "darwin":
    for name in ("objc", "ApplicationServices", "Quartz"):
        try:
            hiddenimports += collect_submodules(name)
        except Exception:  # noqa: BLE001 — sin pyobjc, el control de macOS queda desactivado
            pass
elif sys.platform == "win32":
    try:
        hiddenimports += collect_submodules("pywinauto")
    except Exception:  # noqa: BLE001
        pass

a = Analysis(
    ["../satellite/launcher.py"],
    pathex=[".."],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tests", "tkinter", "mypy", "ruff", "bandit"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="satellite",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="satellite", upx=False)
