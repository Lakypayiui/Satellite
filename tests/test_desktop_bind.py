"""Punto 2 — auditoría del enlace: loopback libre, host externo sólo con token.

Cubre las tres situaciones pedidas: loopback (uso local de siempre), host externo
sin token (se rechaza con motivo) y host externo con token (se sirve, con la API
exigiendo autenticación).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from desktop_support import make_backend  # noqa: E402
from satellite.desktop.__main__ import build_parser, main  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.bind import BindConfigError, is_loopback_host, resolve_bind  # noqa: E402


# --- política de enlace ---


def test_loopback_is_free_and_gets_a_generated_token() -> None:
    host, token = resolve_bind("", "")
    assert host == "127.0.0.1"
    assert token  # el uso local de siempre sigue funcionando sin --token

    for loopback in ("127.0.0.1", "localhost", "::1", "127.5.5.5"):
        h, t = resolve_bind(loopback, "")
        assert h == loopback.lower()
        assert t


def test_is_loopback_host() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.10")
    assert not is_loopback_host("")


def test_external_host_without_token_is_rejected() -> None:
    with pytest.raises(BindConfigError) as info:
        resolve_bind("0.0.0.0", "")
    assert info.value.code == "bind_requires_token"


def test_external_host_with_a_short_token_is_rejected() -> None:
    with pytest.raises(BindConfigError) as info:
        resolve_bind("192.168.1.10", "corto")
    assert info.value.code == "bind_token_too_short"


def test_external_host_with_token_is_accepted() -> None:
    host, token = resolve_bind("0.0.0.0", "t" * 24)
    assert host == "0.0.0.0"
    assert token == "t" * 24


# --- arranque ---


def test_parser_defaults_to_loopback_without_token() -> None:
    args = build_parser().parse_args([])
    assert args.host == ""
    assert args.token == ""


def test_main_refuses_external_host_without_token(capsys) -> None:
    codigo = main(["--host", "0.0.0.0"])

    assert codigo == 4  # no arranca nada
    err = capsys.readouterr().err
    assert "token" in err


def test_external_host_with_token_serves_with_authentication(tmp_path: Path) -> None:
    _host, token = resolve_bind("0.0.0.0", "clave-de-servicio-larga-123456")
    backend = make_backend(tmp_path, token=token)
    client = TestClient(create_app(backend))

    assert client.get("/api/status").status_code == 401  # sin token no sirve datos

    client.headers.update({"Authorization": f"Bearer {token}"})
    assert client.get("/api/status").status_code == 200  # con token válido, sí
