"""API de control local del servicio embebido (vendorizado de ``test_safe_proxy_control.py``).

Cubre: auth con token separado, config redactada sin filtrar secretos, validación del
PATCH (allowlist), enable/disable real del routing y que el PATCH de opciones permitidas
afecte de verdad al manejo del servicio.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from safe_proxy_support import ACCESS_TOKEN, CONTROL_TOKEN, UPSTREAM_KEY, proxy  # noqa: E402, F401


# --------------------------------------------------------------------------- #
# Auth: token de control separado del token de acceso
# --------------------------------------------------------------------------- #
def test_control_requiere_token_separado(proxy) -> None:
    assert proxy.client.get("/control/status").status_code == 401
    assert proxy.client.get(
        "/control/status", headers={"Authorization": "Bearer malo"}
    ).status_code == 401
    # El token de acceso NO sirve para el control.
    assert proxy.client.get("/control/status", headers=proxy.access).status_code == 401
    # El token de control NO sirve para el proxy.
    assert proxy.client.post(
        "/v1/chat/completions",
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hola"}]},
        headers=proxy.control,
    ).status_code == 401
    # El token correcto (Bearer o cabecera dedicada) sí autoriza.
    assert proxy.client.get("/control/status", headers=proxy.control).status_code == 200
    assert proxy.client.get(
        "/control/status", headers={"X-Control-Token": CONTROL_TOKEN}
    ).status_code == 200


def test_control_exige_cliente_loopback(proxy) -> None:
    from fastapi.testclient import TestClient

    remoto = TestClient(proxy.service.app, client=("203.0.113.9", 50000))

    assert remoto.get("/control/status", headers=proxy.control).status_code == 403


# --------------------------------------------------------------------------- #
# Status y config redactada (sin filtrar secretos)
# --------------------------------------------------------------------------- #
def test_status_y_config_no_filtran_secretos(proxy) -> None:
    respuesta = proxy.client.get("/control/status", headers=proxy.control)
    assert respuesta.status_code == 200
    body = respuesta.json()
    assert body["enabled"] is True
    assert body["control"]["available"] is True
    assert body["control"]["token_source"] == "embedded"

    config = proxy.client.get("/control/config", headers=proxy.control)
    assert config.status_code == 200
    raw = json.dumps(config.json())
    # Nunca se devuelve el token de control, el de acceso ni la api key real.
    assert CONTROL_TOKEN not in raw
    assert ACCESS_TOKEN not in raw
    assert UPSTREAM_KEY not in raw
    cfg = config.json()
    # Solo nombres de env / booleanos, no valores secretos.
    assert cfg["upstream"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert cfg["upstream"]["api_key_configured"] is True
    assert "policy" in cfg and "allowed_models" in cfg["policy"]


# --------------------------------------------------------------------------- #
# Validación del PATCH (allowlist real)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"server": {"host": "0.0.0.0"}},            # bind/puerto prohibidos
        {"server": {"port": 9999}},
        {"upstream": {"base_url": "http://evil"}},  # SSRF prohibido
        {"upstream": {"api_key_env": "X"}},         # secretos prohibidos
        {"policy": {"block_regex": ["x"]}},         # fuera de allowlist
        {"policy": {"secrets_action": "nope"}},
        {"policy": {"pii": ["EMAIL", "NOPE"]}},
        {"policy": {"allowed_models": "no-es-lista"}},
        {"policy": {"allowed_models": [""]}},
        {"code": {"obfuscate_python": "si"}},
        {"code": {"other_languages": "maybe"}},
        {"upstream": {"timeout_seconds": 0}},
        {"upstream": {"timeout_seconds": 999999}},
        {"upstream": {"timeout_seconds": "60"}},
        {"secreto": {"x": 1}},
        {"state_file": "/tmp/evil.json"},           # la ruta del estado no se acepta
        {"enabled": False},                          # ni 'enabled' por PATCH
    ],
)
def test_patch_rechaza_invalido(proxy, payload) -> None:
    respuesta = proxy.client.patch("/control/config", json=payload, headers=proxy.control)

    assert respuesta.status_code == 400, payload


def test_patch_acepta_allowlist(proxy) -> None:
    payload = {
        "policy": {
            "allowed_models": ["deepseek-chat", "otro"],
            "block_terms": ["AlfaBeta"],
            "mask_terms": ["Acme"],
            "pii": ["email", "PHONE"],
            "secrets_action": "redact",
        },
        "code": {"strip_comments": False, "other_languages": "allow", "fail_closed": False},
        "upstream": {"timeout_seconds": 42},
    }

    respuesta = proxy.client.patch("/control/config", json=payload, headers=proxy.control)

    assert respuesta.status_code == 200
    cfg = respuesta.json()["config"]
    assert cfg["policy"]["allowed_models"] == ["deepseek-chat", "otro"]
    assert cfg["policy"]["secrets_action"] == "redact"
    assert cfg["policy"]["pii"] == ["EMAIL", "PHONE"]  # normalizado
    assert cfg["code"]["strip_comments"] is False
    assert cfg["upstream"]["timeout_seconds"] == 42
    # No se cambió el bind/puerto.
    assert cfg["server"]["host"] == proxy.service.cfg["server"]["host"]
    assert cfg["server"]["port"] == proxy.service.cfg["server"]["port"]


# --------------------------------------------------------------------------- #
# El PATCH realmente afecta al routing del servicio
# --------------------------------------------------------------------------- #
def test_patch_cambia_routing_real(proxy) -> None:
    proxy.sent.clear()
    assert proxy.chat("hola").status_code == 200  # deepseek-chat permitido al inicio

    respuesta = proxy.client.patch(
        "/control/config", json={"policy": {"allowed_models": ["otro-modelo"]}}, headers=proxy.control
    )
    assert respuesta.status_code == 200

    assert proxy.chat("hola").status_code == 403          # ya no permitido
    assert proxy.chat("hola", model="otro-modelo").status_code == 200


# --------------------------------------------------------------------------- #
# enable / disable altera de verdad el manejo del servicio
# --------------------------------------------------------------------------- #
def test_disable_responde_503_y_control_sigue_vivo(proxy) -> None:
    assert proxy.client.post("/control/disable", headers=proxy.control).status_code == 200

    body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hola"}]}
    assert proxy.client.post("/v1/chat/completions", json=body, headers=proxy.access).status_code == 503
    assert proxy.client.post("/preview", json=body, headers=proxy.access).status_code == 503
    # Los endpoints de control siguen operativos.
    estado = proxy.client.get("/control/status", headers=proxy.control)
    assert estado.status_code == 200
    assert estado.json()["enabled"] is False

    assert proxy.client.post("/control/enable", headers=proxy.control).status_code == 200
    assert proxy.client.post("/v1/chat/completions", json=body, headers=proxy.access).status_code == 200
    assert proxy.client.get("/control/status", headers=proxy.control).json()["enabled"] is True
