"""Política de enlace del backend de escritorio.

Escuchar en loopback (``127.0.0.1``, ``localhost``, ``::1``) es libre: el renderer vive
en la misma máquina. Escuchar en una interfaz **externa** sólo se permite con un token
explícito y suficientemente largo, porque la API exige autenticación: sin token la
configuración se rechaza con un motivo claro en vez de servir abierto a la red.
"""

from __future__ import annotations

import ipaddress

from satellite.desktop.auth import new_token

DEFAULT_HOST = "127.0.0.1"
MIN_TOKEN_LEN = 16


class BindConfigError(ValueError):
    """Configuración de enlace inválida, con código y mensaje legibles."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_loopback_host(host: object) -> bool:
    """True sólo para localhost/loopback (v4 o v6)."""
    h = str(host or "").strip().strip("[]").lower()
    if h in ("localhost", "::1", "0:0:0:0:0:0:0:1"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def resolve_bind(host: object, token: str = "") -> tuple[str, str]:
    """Devuelve ``(host, token)`` validados o lanza :class:`BindConfigError`.

    - Loopback (por defecto): se genera un token si no se dio uno (uso local de siempre).
    - Interfaz externa: exige un token explícito y con longitud mínima; si falta, se
      rechaza la configuración en vez de escuchar sin autenticación real.
    """
    h = str(host or "").strip().strip("[]")
    tok = str(token or "").strip()
    if not h:
        h = DEFAULT_HOST
    if is_loopback_host(h):
        return h.lower(), tok or new_token()
    if not tok:
        raise BindConfigError(
            "bind_requires_token",
            f"escuchar en '{h}' (no loopback) exige un token: "
            "pasa --token <secreto> o quédate en 127.0.0.1",
        )
    if len(tok) < MIN_TOKEN_LEN:
        raise BindConfigError(
            "bind_token_too_short",
            f"el token para escuchar en '{h}' debe tener al menos {MIN_TOKEN_LEN} caracteres",
        )
    return h.lower(), tok
