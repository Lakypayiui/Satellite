"""Saneado end-to-end del servicio embebido (vendorizado de ``test_safe_proxy.py``).

Adaptado al servicio como objeto (``SafeProxyService`` + ``Probe``): cada prueba tiene su
instancia, su carpeta de datos y su upstream de mentira. Se conserva el mismo
comportamiento: bloqueo, ofuscación, enmascarado, restauración y auditoría sin contenido.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from safe_proxy_support import CODE, PROSE, proxy  # noqa: E402, F401  (fixture)


def test_sanitiza_y_restaura(proxy) -> None:
    proxy.sent.clear()

    respuesta = proxy.chat(PROSE + CODE)

    assert respuesta.status_code == 200
    out = json.dumps(proxy.sent["payload"], ensure_ascii=False)
    for secreto in [
        "juan.perez@acme.com", "55 1234 5678", "GOMC800101HDFRRL09", "GOMC800101AB1",
        "4111 1111 1111 1111", "192.168.1.10", "Orion",
        "CalculadoraMargen", "factor_interno", "margen_oculto", "calcular", "precio",
        "Algoritmo propietario", "margen secreto",
    ]:
        assert secreto not in out, f"se filtró: {secreto}"
    assert "__init__" in out and "self" in out  # lo estándar se conserva
    restaurado = respuesta.json()["choices"][0]["message"]["content"]
    for original in [
        "juan.perez@acme.com", "Proyecto Orion", "CalculadoraMargen",
        "factor_interno", "margen_oculto",
    ]:
        assert original in restaurado


def test_bloqueos(proxy) -> None:
    proxy.sent.clear()

    casos = [
        "clave AKIAIOSFODNN7EXAMPLE",
        "usa FormulaSecreta aqui",
        "Copyright Acme. All rights reserved.",
        "```javascript\nconst a = 1;\n```",
        "password = hunter2hunter2",
    ]
    for caso in casos:
        assert proxy.chat(caso).status_code == 403, caso

    imagen = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}],
    }
    assert proxy.client.post(
        "/v1/chat/completions", json=imagen, headers=proxy.access
    ).status_code == 403
    assert "payload" not in proxy.sent  # nada llegó al proveedor


def test_politica_de_modelo_y_tools(proxy) -> None:
    assert proxy.chat("hola", model="otro").status_code == 403
    assert proxy.chat("hola", tools=[{"type": "function"}]).status_code == 403


def test_auth_del_acceso(proxy) -> None:
    body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hola"}]}

    assert proxy.client.post("/v1/chat/completions", json=body).status_code == 401
    assert proxy.client.post(
        "/v1/chat/completions", json=body, headers={"Authorization": "Bearer mal"}
    ).status_code == 401


def test_stream_simulado(proxy) -> None:
    respuesta = proxy.chat("Escribe a juan.perez@acme.com", stream=True)

    assert respuesta.status_code == 200
    assert "juan.perez@acme.com" in respuesta.text
    assert respuesta.text.strip().endswith("[DONE]")


def test_preview_no_envia(proxy) -> None:
    proxy.sent.clear()

    respuesta = proxy.client.post(
        "/preview",
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": PROSE}]},
        headers=proxy.access,
    )

    assert respuesta.status_code == 200
    assert "payload" not in proxy.sent
    assert "juan.perez@acme.com" not in json.dumps(respuesta.json())


def test_auditoria_sin_contenido(proxy) -> None:
    proxy.chat(PROSE + CODE)

    log = (proxy.service.data_dir / "audit.jsonl").read_text(encoding="utf-8")
    assert log.strip()
    for secreto in ["juan.perez", "CalculadoraMargen", "Orion", "AKIA"]:
        assert secreto not in log


def test_routing_deshabilitado_responde_503(proxy) -> None:
    assert proxy.client.post("/control/disable", headers=proxy.control).status_code == 200

    body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hola"}]}
    assert proxy.client.post("/v1/chat/completions", json=body, headers=proxy.access).status_code == 503
    assert proxy.client.post("/preview", json=body, headers=proxy.access).status_code == 503
    # El control sigue vivo.
    assert proxy.client.get("/control/status", headers=proxy.control).status_code == 200

    assert proxy.client.post("/control/enable", headers=proxy.control).status_code == 200
    assert proxy.client.post("/v1/chat/completions", json=body, headers=proxy.access).status_code == 200
