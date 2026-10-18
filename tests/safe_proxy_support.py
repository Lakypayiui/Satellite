"""Apoyo compartido para las pruebas del Safe Proxy embebido.

Construye instancias **aisladas** del servicio (config propia, carpeta de datos temporal)
y un ``TestClient`` que simula llegar desde loopback (la API de control lo exige). Nada de
estado global: cada prueba tiene su servicio.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.safe_proxy.service import SafeProxyService  # noqa: E402

ACCESS_TOKEN = "a" * 32
CONTROL_TOKEN = "c" * 32
UPSTREAM_KEY = "sk-fake-upstream-key-1234567890"

# Réplica de la config standalone del origen (mismas claves que config.yaml).
SERVICE_CONFIG: dict = {
    "upstream": {"base_url": "https://example.invalid", "api_key_env": "DEEPSEEK_API_KEY"},
    "server": {"host": "127.0.0.1", "port": 8787, "audit_log": "audit.jsonl"},
    "policy": {
        "allowed_models": ["deepseek-chat"],
        "allow_tools": False,
        "block_terms": ["FormulaSecreta"],
        "block_regex": ["(?i)all rights reserved"],
        "secrets_action": "block",
        "mask_terms": ["Proyecto Orion"],
        "pii": ["EMAIL", "PHONE", "CURP", "RFC", "CLABE", "CARD", "IP"],
    },
    "code": {
        "obfuscate_python": True,
        "strip_comments": True,
        "other_languages": "block",
        "fail_closed": True,
    },
}

PROSE = (
    "Revisa esto del Proyecto Orion. Escribe a juan.perez@acme.com o al 55 1234 5678. "
    "CURP GOMC800101HDFRRL09, RFC GOMC800101AB1, tarjeta 4111 1111 1111 1111, host 192.168.1.10.\n"
)

CODE = '''```python
# Algoritmo propietario de Proyecto Orion
class CalculadoraMargen:
    """Calcula el margen secreto del cliente."""
    def __init__(self, factor_interno):
        self.factor_interno = factor_interno

    def calcular(self, precio):
        margen_oculto = precio * self.factor_interno
        return margen_oculto
```'''


def make_service(
    data_dir: Path | str,
    config: dict | None = None,
    *,
    access_token: str = ACCESS_TOKEN,
    control_token: str = CONTROL_TOKEN,
    upstream_api_key: str = UPSTREAM_KEY,
) -> SafeProxyService:
    """Servicio embebido aislado con su carpeta de datos."""
    target = Path(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    return SafeProxyService(
        config if config is not None else SERVICE_CONFIG,
        access_token=access_token,
        control_token=control_token,
        data_dir=target,
        upstream_api_key=upstream_api_key,
    )


class Probe:
    """Servicio + cliente loopback + cabeceras y upstream de mentira."""

    def __init__(self, service: SafeProxyService) -> None:
        self.service = service
        self.client = TestClient(service.app, client=("127.0.0.1", 50000))
        self.sent: dict = {}
        self.access = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        self.control = {"Authorization": f"Bearer {CONTROL_TOKEN}"}

    def chat(self, content, model="deepseek-chat", **extra):
        body = {"model": model, "messages": [{"role": "user", "content": content}], **extra}
        return self.client.post("/v1/chat/completions", json=body, headers=self.access)

    def install_fake_upstream(self) -> None:
        """Upstream de mentira que hace eco del mensaje saneado (sin red)."""

        async def fake(payload):
            self.sent["payload"] = payload
            echo = payload["messages"][-1]["content"]
            return 200, {
                "id": "c1",
                "object": "chat.completion",
                "created": 1,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": echo},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            }

        self.service._call_upstream = fake  # type: ignore[method-assign]


@pytest.fixture
def proxy(tmp_path) -> Probe:
    """Servicio nuevo por prueba, con upstream simulado y cliente loopback."""
    probe = Probe(make_service(tmp_path / "data"))
    probe.install_fake_upstream()
    return probe
