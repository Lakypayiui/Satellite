"""Punto 1 — auditoría de secretos: ni en JSON, ni en errores, ni en logs.

Comprueba que las vistas de configuración/estado del backend redactan las claves
(por nombre de campo y por valor con forma de credencial), que la configuración
interna no se altera en disco y que un secreto que viaje en una excepción sale
enmascarado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from desktop_support import FakeSession, make_backend  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import redact, redact_text  # noqa: E402

KEY = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
ROLE_KEY = "sk-proj-ROLEONLYSECRET1234567890abcd"


class LeakyStatusSession(FakeSession):
    """El estado falla con un mensaje que trae la clave: no debe salir."""

    def status(self):
        raise RuntimeError(f"no se pudo leer: api_key={KEY}")


class LeakyRuntimeSession(FakeSession):
    """El runtime falla con la clave en el mensaje: el estado la redacta."""

    def runtime(self):
        raise RuntimeError(f"sin proveedor: api_key={KEY}")


def client_for(backend) -> TestClient:
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {backend.token}"})
    return client


def write_config(tmp_path: Path, data: dict) -> Path:
    config = tmp_path / ".satellite" / "config" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(data), encoding="utf-8")
    return config


# --- redacción de la vista ---


def test_config_and_roles_never_carry_the_key(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    write_config(
        tmp_path,
        {
            "name": "openai",
            "model": "m",
            "api_key": KEY,
            "llm": {
                "provider_id": "p1",
                "orchestrator": {"provider": "p1", "model": "m", "api_key": ROLE_KEY},
            },
        },
    )
    client = client_for(backend)

    for ruta in ("/api/config", "/api/providers", "/api/roles", "/api/status"):
        cuerpo = client.get(ruta).text
        assert KEY not in cuerpo, ruta
        assert ROLE_KEY not in cuerpo, ruta

    config = client.get("/api/config").json()["config"]
    assert config["api_key_set"] is True
    assert "api_key" not in config

    roles = client.get("/api/roles").json()["configured"]
    assert roles["orchestrator"]["api_key_set"] is True
    assert "api_key" not in roles["orchestrator"]


def test_the_internal_config_file_keeps_the_key(tmp_path: Path) -> None:
    """La redacción es sólo de la vista: el archivo interno conserva la clave."""
    backend = make_backend(tmp_path)
    config = write_config(tmp_path, {"api_key": KEY, "name": "openai", "model": "m"})

    client_for(backend).get("/api/config")

    assert json.loads(config.read_text(encoding="utf-8"))["api_key"] == KEY


def test_a_saved_global_provider_hides_the_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    providers = tmp_path / "home" / "config" / "providers.json"
    providers.parent.mkdir(parents=True, exist_ok=True)
    providers.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "p1",
                        "name": "openai",
                        "model": "m",
                        "base_url": "https://example.invalid/v1",
                        "api_key": KEY,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = client_for(make_backend(tmp_path))

    cuerpo = client.get("/api/providers").text
    proveedores = client.get("/api/providers").json()["providers"]

    assert proveedores and proveedores[0]["api_key_set"] is True
    assert KEY not in cuerpo


# --- redacción de errores ---


def test_a_state_error_comes_out_redacted(tmp_path: Path) -> None:
    client = client_for(make_backend(tmp_path, factory=LeakyStatusSession))

    respuesta = client.get("/api/status")

    assert respuesta.status_code == 503
    assert KEY not in respuesta.text
    assert "[REDACTED]" in respuesta.json()["error"]["message"]


def test_a_runtime_error_is_redacted_in_status(tmp_path: Path) -> None:
    client = client_for(make_backend(tmp_path, factory=LeakyRuntimeSession))

    estado = client.get("/api/status").json()

    assert estado["runtime_ready"] is False
    assert KEY not in json.dumps(estado)
    assert "[REDACTED]" in estado["runtime_error"]


# --- primitivas de redacción (las que usan respuestas y logs) ---


def test_redact_masks_by_field_and_by_value() -> None:
    salida = redact(
        {
            "api_key": KEY,
            "base_url": "https://api.example.com/v1",
            "nested": {"token": "x", "note": f"clave {KEY} y url https://api.example.com"},
        }
    )

    assert salida["api_key_set"] is True and "api_key" not in salida
    assert salida["base_url"] == "https://api.example.com/v1"  # lo legítimo no se toca
    assert salida["nested"]["token_set"] is True
    assert KEY not in salida["nested"]["note"]
    assert "https://api.example.com" in salida["nested"]["note"]


def test_redact_text_masks_known_families_but_not_urls() -> None:
    assert redact_text(f"usa {KEY} para probar") == "usa [REDACTED] para probar"
    assert redact_text("https://api.example.com/v1") == "https://api.example.com/v1"
    assert redact_text("modelo gpt-4o · sk-learn") == "modelo gpt-4o · sk-learn"
