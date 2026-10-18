"""Persistencia del servicio embebido (vendorizado de ``test_safe_proxy_persistence.py``).

Cubre: persistir + recargar (equivale a reiniciar), rechazo de estado corrupto con default
seguro, fallo atómico sin éxito falso (rollback), ruta no aceptada del cliente, ausencia de
secretos en el estado y que la config base no se reescribe.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from safe_proxy_support import (  # noqa: E402
    ACCESS_TOKEN,
    CONTROL_TOKEN,
    SERVICE_CONFIG,
    UPSTREAM_KEY,
    make_service,
)

CH = {"Authorization": f"Bearer {CONTROL_TOKEN}"}


def _client(service) -> TestClient:
    return TestClient(service.app, client=("127.0.0.1", 50000))


def _states(data: Path) -> Path:
    return data / "state.json"


# --------------------------------------------------------------------------- #
# Persistencia y recarga (equivalente a reiniciar el servicio)
# --------------------------------------------------------------------------- #
def test_patch_persiste_y_recarga(tmp_path) -> None:
    data = tmp_path / "data"
    service = make_service(data)
    client = _client(service)

    respuesta = client.patch(
        "/control/config",
        json={
            "policy": {"allowed_models": ["otro"], "block_terms": ["SecretoX"]},
            "upstream": {"timeout_seconds": 30},
        },
        headers=CH,
    )
    assert respuesta.status_code == 200
    assert _states(data).exists()
    guardado = json.loads(_states(data).read_text(encoding="utf-8"))
    assert guardado["enabled"] is True
    assert guardado["config"]["policy"]["allowed_models"] == ["otro"]
    assert guardado["config"]["upstream"]["timeout_seconds"] == 30

    # "Reinicio": instancia nueva sobre la misma carpeta, con la config base de nuevo.
    otra = make_service(data)

    assert otra.cfg["policy"]["allowed_models"] == ["otro"]
    assert otra.cfg["policy"]["block_terms"] == ["SecretoX"]
    assert otra.cfg["upstream"]["timeout_seconds"] == 30
    assert otra.routing_enabled() is True


def test_disable_se_persiste_y_recarga(tmp_path) -> None:
    data = tmp_path / "data"
    service = make_service(data)

    assert _client(service).post("/control/disable", headers=CH).status_code == 200
    assert json.loads(_states(data).read_text(encoding="utf-8"))["enabled"] is False

    otra = make_service(data)

    assert otra.routing_enabled() is False  # se respetó lo persistido


def test_estado_no_contiene_secretos(tmp_path) -> None:
    data = tmp_path / "data"
    service = make_service(data)
    client = _client(service)

    client.patch("/control/config", json={"policy": {"mask_terms": ["Acme"]}}, headers=CH)
    client.post("/control/disable", headers=CH)

    raw = _states(data).read_text(encoding="utf-8")
    for secreto in [CONTROL_TOKEN, ACCESS_TOKEN, UPSTREAM_KEY]:
        assert secreto not in raw
    # Solo el subconjunto allowlisted: nada de bind/puerto/base_url/api_key_env.
    assert "host" not in raw and "port" not in raw and "base_url" not in raw
    assert "api_key_env" not in raw


def test_state_file_junto_a_los_datos_del_servicio(tmp_path) -> None:
    data = tmp_path / "data"
    service = make_service(data)

    assert _states(data).parent == service.data_dir
    # El config base no se reescribe al persistir.
    base = Path(service.data_dir) / "config.json"
    before = base.read_text(encoding="utf-8") if base.exists() else None
    _client(service).patch("/control/config", json={"upstream": {"timeout_seconds": 15}}, headers=CH)
    after = base.read_text(encoding="utf-8") if base.exists() else None
    assert before == after


# --------------------------------------------------------------------------- #
# Default seguro y rechazo de estado corrupto
# --------------------------------------------------------------------------- #
def test_sin_archivo_usa_default_seguro(tmp_path) -> None:
    data = tmp_path / "data"
    _states(data).unlink(missing_ok=True)

    service = make_service(data)

    assert service.routing_enabled() is True
    assert service.cfg["policy"]["allowed_models"] == SERVICE_CONFIG["policy"]["allowed_models"]


@pytest.mark.parametrize(
    "raw",
    [
        "{ no es json",
        "[]",
        '"texto"',
        '{"version": 1, "enabled": "si"}',
        '{"version": 99, "enabled": true}',
        '{"version": 1, "enabled": true, "config": []}',
        '{"version": 1, "enabled": true, "config": {"policy": {"secrets_action": "nope"}}}',
        '{"version": 1, "enabled": true, "config": {"server": {"host": "0.0.0.0"}}}',
    ],
)
def test_estado_corrupto_se_rechaza(tmp_path, raw) -> None:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    _states(data).write_text(raw, encoding="utf-8")

    service = make_service(data)  # no debe lanzar ni aplicar basura

    assert service.routing_enabled() is True
    assert service.cfg["policy"]["allowed_models"] == ["deepseek-chat"]
    assert service.cfg["policy"]["secrets_action"] == "block"


# --------------------------------------------------------------------------- #
# Fallo de escritura: sin éxito falso y con rollback
# --------------------------------------------------------------------------- #
def test_fallo_atomico_patch_no_aplica(tmp_path, monkeypatch) -> None:
    data = tmp_path / "data"
    service = make_service(data)
    client = _client(service)

    def boom(*_a, **_k):
        raise OSError("disco lleno")

    monkeypatch.setattr("satellite.safe_proxy.core.os.replace", boom)
    respuesta = client.patch("/control/config", json={"policy": {"allowed_models": ["otro"]}}, headers=CH)

    assert respuesta.status_code == 500
    assert service.cfg["policy"]["allowed_models"] == ["deepseek-chat"]  # sin cambio
    assert not _states(data).exists()  # temp limpiado, sin archivo a medias


def test_fallo_atomico_disable_no_cambia_estado(tmp_path, monkeypatch) -> None:
    data = tmp_path / "data"
    service = make_service(data)
    client = _client(service)

    def boom(*_a, **_k):
        raise OSError("disco lleno")

    monkeypatch.setattr("satellite.safe_proxy.core.os.replace", boom)
    assert client.post("/control/disable", headers=CH).status_code == 500

    assert service.routing_enabled() is True  # no cambió


@pytest.mark.skipif(os.name == "nt", reason="permisos POSIX no aplican en Windows")
def test_permisos_0600(tmp_path) -> None:
    data = tmp_path / "data"
    service = make_service(data)

    _client(service).patch("/control/config", json={"upstream": {"timeout_seconds": 20}}, headers=CH)

    assert (_states(data).stat().st_mode & 0o777) == 0o600
