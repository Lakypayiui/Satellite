"""Arranque del backend: ``python -m satellite.desktop``.

Anuncia **una sola** línea JSON en stdout con el puerto real: es lo único que el proceso
principal de Electron necesita leer. El token no se imprime nunca: Electron lo genera y
lo entrega por stdin (``--token-stdin``), así que no queda en logs, en la lista de
argumentos del proceso ni en la salida estándar. Todo lo demás (y todos los logs) va a
stderr, para que esa línea sea siempre la primera y no se confunda con nada.

Enlace (``--host``): por defecto loopback, que es el uso local de siempre. Escuchar en una
interfaz **externa** exige un token explícito (``--token`` o ``--token-stdin``); sin él la
configuración se rechaza en vez de servir abierto a la red.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys

from satellite.desktop.bind import DEFAULT_HOST, BindConfigError, resolve_bind

MAX_TOKEN_LINE = 4096


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
        help=(
            f"interfaz de escucha (por defecto {DEFAULT_HOST}); "
            "fuera de loopback exige --token o --token-stdin"
        ),
    )
    parser.add_argument(
        "--token",
        default="",
        help="token de acceso; fuera de loopback exige --token o --token-stdin",
    )
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="lee el token de una línea de stdin (lo usa Electron; nunca se imprime)",
    )
    return parser


def read_token_stdin() -> str:
    """Lee el token de una línea de stdin; vacío si no hay una tubería válida.

    Electron entrega el token por este canal para que no aparezca en ``stdout`` ni en
    los argumentos del proceso. Se acota la lectura para no aceptar una entrada
    ilimitada y se rechaza una terminal interactiva: ese uso no es el previsto.
    """

    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        linea = sys.stdin.readline(MAX_TOKEN_LINE + 1)
    except (OSError, ValueError):
        return ""
    if len(linea) > MAX_TOKEN_LINE:
        return ""
    return linea.strip()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.token and args.token_stdin:
        print("--token y --token-stdin son mutuamente excluyentes", file=sys.stderr)
        return 4

    token = args.token
    if args.token_stdin:
        token = read_token_stdin()
        if not token:
            print("--token-stdin no recibió un token por stdin", file=sys.stderr)
            return 4

    # La política de enlace se decide antes de importar dependencias o abrir el socket:
    # una configuración rechazada no deja nada escuchando.
    try:
        host, token = resolve_bind(args.host, token)
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

    # Sólo el puerto: el token lo conoce quien nos lo entregó por stdin y nunca sale
    # de este proceso.
    print(json.dumps({"port": sock.getsockname()[1]}), flush=True)

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
