"""Punto 2 — auditoría del enlace: loopback libre, host externo sólo con token.

Cubre las tres situaciones pedidas: loopback (uso local de siempre), host externo
sin token (se rechaza con motivo) y host externo con token (se sirve, con la API
exigiendo autenticación).
"""

from __future__ import annotations

import io
import json
import sys
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
    assert args.token_stdin is False


def test_main_refuses_external_host_without_token(capsys) -> None:
    codigo = main(["--host", "0.0.0.0"])

    assert codigo == 4  # no arranca nada
    err = capsys.readouterr().err
    assert "token" in err


def test_main_rejects_token_and_token_stdin_together(capsys) -> None:
    codigo = main(["--token", "x" * 24, "--token-stdin"])

    assert codigo == 4
    assert "mutuamente excluyentes" in capsys.readouterr().err


def test_main_rejects_empty_token_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    codigo = main(["--token-stdin"])

    assert codigo == 4
    assert "no recibió un token" in capsys.readouterr().err


def test_external_host_validates_the_token_received_by_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("corto\n"))

    codigo = main(["--host", "0.0.0.0", "--token-stdin"])

    assert codigo == 4
    assert "al menos" in capsys.readouterr().err


def test_main_with_token_stdin_prints_only_the_port(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    uvicorn = pytest.importorskip("uvicorn")
    token = "t" * 64
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{token}\n"))

    def servidor_sin_arrancar(self, sockets=None) -> None:
        for sock in sockets or []:
            sock.close()

    monkeypatch.setattr(uvicorn.Server, "run", servidor_sin_arrancar)

    codigo = main(
        [
            "--token-stdin",
            "--project",
            str(tmp_path / "proyecto"),
            "--home",
            str(tmp_path / "home"),
        ]
    )

    capturado = capsys.readouterr()
    assert codigo == 0
    assert token not in capturado.out
    assert token not in capturado.err
    lineas = capturado.out.splitlines()
    assert len(lineas) == 1
    handshake = json.loads(lineas[0])
    assert set(handshake) == {"port"}
    assert 0 < handshake["port"] < 65536


def test_external_host_with_token_serves_with_authentication(tmp_path: Path) -> None:
    _host, token = resolve_bind("0.0.0.0", "clave-de-servicio-larga-123456")
    backend = make_backend(tmp_path, token=token)
    client = TestClient(create_app(backend))

    assert client.get("/api/status").status_code == 401  # sin token no sirve datos

    client.headers.update({"Authorization": f"Bearer {token}"})
    assert client.get("/api/status").status_code == 200  # con token válido, sí
