"""Autenticación del renderer: token local aleatorio, comparado en tiempo constante.

El token vive sólo en el proceso que arranca el backend (y en el proceso principal de
Electron). No se escribe en disco, no se registra en logs y **nunca** viaja a la interfaz.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable

from fastapi import Header, HTTPException

UNAUTHORIZED = {
    "error": {
        "code": "unauthorized",
        "message": "falta el token local de la app (cabecera Authorization)",
    }
}


def new_token() -> str:
    """Token del arranque."""
    return secrets.token_urlsafe(32)


def token_matches(expected: str, provided: str | None) -> bool:
    """True sólo si la cabecera trae exactamente ``Bearer <token>``."""
    if not expected or not provided:
        return False
    scheme, _, value = provided.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return False
    return hmac.compare_digest(expected, value.strip())


def make_dependency(token: str) -> Callable[..., Awaitable[None]]:
    """Dependencia de FastAPI que exige el token; se aplica a todo el router."""

    async def check(authorization: str | None = Header(default=None)) -> None:
        if not token_matches(token, authorization):
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)

    return check
