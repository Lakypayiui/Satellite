"""Ejecutable único ``satellite``: la CLI (con la TUI y el servidor MCP) y el backend de
la app de escritorio.

Es el punto de entrada del comando instalado (``[project.scripts]``) y del ejecutable
congelado que viaja dentro de la app: así la app de escritorio, la terminal y la TUI usan
exactamente el mismo código. El backend del escritorio no es un comando de usuario: lo
lanza Electron con un subcomando interno.
"""

from __future__ import annotations

import sys

# Subcomando interno con el que la app de escritorio arranca su backend local.
DESKTOP_BACKEND = "desktop-backend"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [DESKTOP_BACKEND]:
        from satellite.desktop.__main__ import main as desktop_main

        return desktop_main(args[1:])
    from satellite.cli.app import app

    # typer termina con SystemExit (código de salida del comando).
    app(args=args, prog_name="satellite")
    return 0


if __name__ == "__main__":
    import multiprocessing

    # Necesario en el ejecutable congelado si algo usa multiprocessing (no-op si no).
    multiprocessing.freeze_support()
    raise SystemExit(main())
