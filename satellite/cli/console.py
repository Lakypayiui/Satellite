"""Salida de consola tolerante a codificaciones legacy (cp1252/cp850)."""

from __future__ import annotations

import sys

import typer


def say(message: str = "") -> None:
    """``typer.echo`` que no revienta con consolas no-UTF8.

    Sin esto, un carácter como ``✓`` lanza UnicodeEncodeError cuando la salida no
    es UTF-8 (consolas legacy de Windows o salida redirigida).
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        message.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        message = message.encode(encoding, "replace").decode(encoding, "replace")
    typer.echo(message)
