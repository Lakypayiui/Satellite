"""Arranque del backend: ``python -m satellite.desktop``.

Imprime **una sola** línea JSON en stdout con el puerto real y el token: es lo único que
el proceso principal de Electron necesita leer. Todo lo demás (y todos los logs) va a
stderr, para que esa línea sea siempre la primera y no se confunda con nada.

Enlace (``--host``): por defecto loopback, que es el uso local de siempre. Escuchar en una
interfaz **externa** exige un token explícito (``--token``); sin él la configuración se
rechaza en vez de servir abierto a la red.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys

from satellite.desktop.bind import DEFAULT_HOST, BindConfigError, resolve_bind


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="satellite.desktop",
        description="Backend local de la app de escritorio de Satellite",
    )
    parser.add_argument("--project", default="", help="carpeta del proyecto a abrir")
    parser.add_argument("--home", default="", help="SATELLITE_HOME alternativo")
    parser.add_argument(
        "--port", type=int, default=0, help="puerto (0 = el que elija el sistema)"
    )
    parser.add_argument(
        "--host",
        default="",
        help=f"interfaz de escucha (por defecto {DEFAULT_HOST}); fuera de loopback exige --token",
    )
    parser.add_argument(
        "--token",
        default="",
        help="token de acceso; obligatorio si --host no es loopback",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # La política de enlace se decide antes de importar dependencias o abrir el socket:
    # una configuración rechazada no deja nada escuchando.
    try:
        host, token = resolve_bind(args.host, args.token)
    except BindConfigError as exc:
        print(exc.message, file=sys.stderr)
        return 4

    from satellite.desktop import require_dependencies

    try:
        require_dependencies()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    import uvicorn

    from satellite.desktop.app import create_app
    from satellite.desktop.backend import DesktopBackend

    backend = DesktopBackend(args.project or None, home=args.home or None, token=token)
    app = create_app(backend)

    # Se enlaza aquí el socket para poder anunciar el puerto de verdad (con --port 0 no
    # se sabe hasta que alguien lo pide) y para que escuche donde se pidió (loopback por
    # defecto; una interfaz externa sólo llega aquí con token explícito).
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, args.port))
    except OSError as exc:
        print(f"no se pudo escuchar en {host}:{args.port}: {exc}", file=sys.stderr)
        return 3
    sock.listen(128)

    print(
        json.dumps({"port": sock.getsockname()[1], "token": backend.token}),
        flush=True,
    )

    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", access_log=False, lifespan="off")
    )
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        try:
            asyncio.run(backend.close())
        except RuntimeError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
