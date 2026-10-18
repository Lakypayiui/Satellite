"""Casilla «API keys»: detección/protección de claves de API.

Cubre la opción real ``policy.api_keys`` (integrada en el saneador y en la allowlist del
PATCH, compartida por el modo interno y el servidor):

- activa (por defecto, fail-safe): las claves de API se detectan y se tratan según
  ``policy.secrets_action`` (bloquear o enmascarar);
- apagada: pasan sin tocar, mientras el resto de reglas de secretos sigue aplicándose;
- persistencia y recarga, redacción sin registrar contenido, y UI/i18n de la casilla.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.core.messages import LLMRequest, LLMResponse, Message, Role  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.providers.base import LLMProvider  # noqa: E402
from satellite.safe_proxy.interceptor import SanitizingProvider, reset_cache  # noqa: E402
from satellite.safe_proxy.sanitizer import Blocked, Sanitizer, Vault  # noqa: E402
from safe_proxy_support import (  # noqa: E402
    CONTROL_TOKEN,
    SERVICE_CONFIG,
    make_service,
    proxy,  # noqa: F401  (fixture)
)

RAIZ = Path(__file__).resolve().parents[1]
TOKEN = "token-local-de-prueba"

# Claves de ejemplo por familia (largas y bien formadas, como las reales).
# ``AIza`` + 35 caracteres = 39, la forma real de una clave de Google.
_GOOGLE = "AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe"  # ejemplo con la forma real de Google
assert len(_GOOGLE) == 39

CLAVES = {
    "openai/deepseek (sk-)": "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
    "google (AIza)": _GOOGLE,
    "xai (xai-)": "xai-abcdefghijklmnopqrstuvwxyz012345",
    "huggingface (hf_)": "hf_abcdefghijklmnopqrstuvwxyz0123456789",
    "groq (gsk_)": "gsk_abcdefghijklmnopqrstuvwxyz012345",
    "replicate (r8_)": "r8_abcdefghijklmnopqrstuvwxyz012345",
    "perplexity (pplx-)": "pplx-abcdefghijklmnopqrstuvwxyz012345",
}


def _cfg(api_keys: object = ..., secrets_action: str = "block") -> dict:
    """Config mínima del saneador; ``...`` = no poner la clave (default del core)."""
    policy = {
        "allowed_models": ["deepseek-chat"],
        "block_terms": [],
        "block_regex": [],
        "mask_terms": [],
        "pii": ["EMAIL"],
        "secrets_action": secrets_action,
    }
    if api_keys is not ...:
        policy["api_keys"] = api_keys
    return {
        "upstream": {"base_url": "https://example.invalid"},
        "server": {"host": "127.0.0.1", "port": 8787},
        "policy": policy,
        "code": {"obfuscate_python": True, "strip_comments": True, "other_languages": "block"},
    }


# --------------------------------------------------------------------------- #
# Saneador: la opción es real
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("nombre,clave", list(CLAVES.items()))
def test_api_keys_activo_detecta_la_familia(nombre, clave) -> None:
    sanitizer = Sanitizer(_cfg(True))

    with pytest.raises(Blocked) as excinfo:
        sanitizer.check_blocked(f"usa esta clave {clave} para probar")

    assert excinfo.value.category == "secret"


def test_api_keys_activo_por_defecto_fail_safe() -> None:
    """Sin la clave en la config (o con basura) se protege: fail-safe."""
    for valor in (..., "sí", 1, None):
        sanitizer = Sanitizer(_cfg(valor))
        assert sanitizer.api_keys is True
        with pytest.raises(Blocked):
            sanitizer.check_blocked("clave sk-proj-abcdefghijklmnopqrstuvwxyz012345")


@pytest.mark.parametrize("nombre,clave", list(CLAVES.items()))
def test_api_keys_apagado_deja_pasar_la_clave(nombre, clave) -> None:
    sanitizer = Sanitizer(_cfg(False))
    texto = f"usa esta clave {clave} para probar"

    sanitizer.check_blocked(texto)  # no bloquea
    limpio = sanitizer.run([texto], Vault())

    assert limpio == [texto]  # ni se enmascara: pasa tal cual


def test_api_keys_apagado_conserva_las_demas_reglas() -> None:
    """Apagar las claves de API no desactiva el resto de la protección."""
    sanitizer = Sanitizer(_cfg(False))

    for texto in (
        "clave AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "password = hunter2hunter2",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    ):
        with pytest.raises(Blocked):
            sanitizer.check_blocked(texto)


def test_api_keys_con_redact_enmascara_y_restaura() -> None:
    sanitizer = Sanitizer(_cfg(True, secrets_action="redact"))
    clave = CLAVES["openai/deepseek (sk-)"]

    vault = Vault()
    limpio = sanitizer.run([f"mi clave es {clave}"], vault)

    assert clave not in limpio[0]
    assert "<SECRET_1>" in limpio[0]
    assert vault.restore(limpio[0]) == f"mi clave es {clave}"  # se restaura en la respuesta


def test_api_keys_apagado_con_redact_no_enmascara_esa_familia() -> None:
    sanitizer = Sanitizer(_cfg(False, secrets_action="redact"))
    clave = CLAVES["openai/deepseek (sk-)"]

    limpio = sanitizer.run([f"mi clave es {clave}"], Vault())

    assert clave in limpio[0]  # documentado: con la casilla apagada pasa sin tocar
    # Pero otra regla de secretos sí se enmascara.
    limpio2 = sanitizer.run(["token ghp_abcdefghijklmnopqrstuvwxyz0123456789"], Vault())
    assert "ghp_" not in limpio2[0]


# --------------------------------------------------------------------------- #
# Servicio: configuración, persistencia y recarga
# --------------------------------------------------------------------------- #
def test_patch_apaga_api_keys_persiste_y_recarga(tmp_path) -> None:
    data = tmp_path / "data"
    service = make_service(data)
    client = TestClient(service.app, client=("127.0.0.1", 50000))
    control = {"Authorization": f"Bearer {CONTROL_TOKEN}"}

    aplicado = client.patch("/control/config", json={"policy": {"api_keys": False}}, headers=control)

    assert aplicado.status_code == 200
    assert aplicado.json()["config"]["policy"]["api_keys"] is False
    guardado = json.loads((data / "state.json").read_text(encoding="utf-8"))
    assert guardado["config"]["policy"]["api_keys"] is False

    # "Reinicio": instancia nueva sobre la misma carpeta; la opción sigue apagada.
    otra = make_service(data)

    assert otra.cfg["policy"]["api_keys"] is False
    assert otra.core.sanitizer.api_keys is False


def test_el_patch_rechaza_api_keys_no_booleano(tmp_path) -> None:
    data = tmp_path / "data"
    service = make_service(data)
    client = TestClient(service.app, client=("127.0.0.1", 50000))
    control = {"Authorization": f"Bearer {CONTROL_TOKEN}"}

    for valor in ("si", 1, None, ["si"]):
        respuesta = client.patch(
            "/control/config", json={"policy": {"api_keys": valor}}, headers=control
        )
        assert respuesta.status_code == 400, valor


def test_el_servicio_respeta_la_casilla(proxy) -> None:
    clave = CLAVES["google (AIza)"]

    # Activa (por defecto): bloquea.
    assert proxy.chat(f"usa {clave}").status_code == 403
    assert "payload" not in proxy.sent

    # Apagada: pasa y llega al proveedor.
    assert proxy.client.patch(
        "/control/config", json={"policy": {"api_keys": False}}, headers=proxy.control
    ).status_code == 200
    proxy.sent.clear()
    assert proxy.chat(f"usa {clave}").status_code == 200
    assert clave in json.dumps(proxy.sent["payload"])

    # Y reactivarla vuelve a bloquear.
    proxy.client.patch("/control/config", json={"policy": {"api_keys": True}}, headers=proxy.control)
    assert proxy.chat(f"usa {clave}").status_code == 403


def test_la_auditoria_nunca_registra_la_clave(proxy) -> None:
    clave = CLAVES["groq (gsk_)"]

    proxy.chat(f"mi clave es {clave}")  # bloqueada
    proxy.client.patch(
        "/control/config", json={"policy": {"api_keys": False}}, headers=proxy.control
    )
    proxy.chat(f"mi clave es {clave}")  # pasa

    log = (proxy.service.data_dir / "audit.jsonl").read_text(encoding="utf-8")
    assert log.strip()
    assert clave not in log
    assert "gsk_" not in log


# --------------------------------------------------------------------------- #
# Backend: la casilla aplica al modo interno y se refleja al recargar
# --------------------------------------------------------------------------- #
class _Session:
    def __init__(self, root, home=None) -> None:
        self.root = Path(root).resolve()


class _FakeProvider(LLMProvider):
    def __init__(self) -> None:
        self.seen: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.seen.append(request)
        return LLMResponse(content="ok")


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    reset_cache()
    backend = DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=_Session,
        recents_path=tmp_path / "recents.json",
    )
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {backend.token}"})
    yield client, backend
    asyncio.run(backend.close())
    reset_cache()


def _peticion(texto: str) -> LLMRequest:
    return LLMRequest(messages=(Message(role=Role.USER, content=texto),))


def test_la_casilla_aplica_al_modo_interno(entorno) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})  # modo interno activo

    clave = CLAVES["xai (xai-)"]

    # Activa por defecto: el interceptor bloquea la clave.
    proveedor = _FakeProvider()
    with pytest.raises(Exception):
        asyncio.run(SanitizingProvider(proveedor).complete(_peticion(f"usa {clave}")))
    assert proveedor.seen == []

    # Casilla apagada desde Ajustes: la clave pasa, el resto sigue protegido.
    assert client.patch(
        "/api/safe-proxy/config", json={"policy": {"api_keys": False}}
    ).status_code == 200

    proveedor2 = _FakeProvider()
    asyncio.run(SanitizingProvider(proveedor2).complete(_peticion(f"usa {clave}")))
    assert clave in proveedor2.seen[0].messages[0].content

    proveedor3 = _FakeProvider()
    with pytest.raises(Exception):
        asyncio.run(
            SanitizingProvider(proveedor3).complete(_peticion("password = hunter2hunter2"))
        )


def test_la_vista_refleja_la_casilla_al_recargar(entorno) -> None:
    client, _backend = entorno

    inicial = client.get("/api/safe-proxy").json()
    assert inicial["config"]["policy"]["api_keys"] is True  # fail-safe por defecto

    client.patch("/api/safe-proxy/config", json={"policy": {"api_keys": False}})

    despues = client.get("/api/safe-proxy").json()
    assert despues["config"]["policy"]["api_keys"] is False


def test_el_backend_rechaza_api_keys_no_booleano(entorno) -> None:
    respuesta = entorno[0].patch("/api/safe-proxy/config", json={"policy": {"api_keys": "si"}})

    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "safe_proxy_invalid"


# --------------------------------------------------------------------------- #
# UI / i18n
# --------------------------------------------------------------------------- #
def test_i18n_de_la_casilla_api_keys() -> None:
    i18n = (RAIZ / "desktop" / "src" / "i18n.tsx").read_text(encoding="utf-8")
    settings = (RAIZ / "desktop" / "src" / "Settings.tsx").read_text(encoding="utf-8")

    bloques = dict(re.findall(r'"([^"]+)":\s*\{(.*?)\}\s*,\s*\n', i18n, re.S))
    for clave in (
        "settings.safeProxy.config.apiKeys",
        "settings.safeProxy.config.apiKeys.aria",
        "settings.safeProxy.config.apiKeys.hint",
    ):
        assert clave in bloques, clave
        assert re.search(r"\bes:", bloques[clave]), clave
        assert re.search(r"\ben:", bloques[clave]), clave

    # El texto visible de la casilla es exactamente "API keys".
    assert re.search(r'"settings\.safeProxy\.config\.apiKeys":\s*\{[^}]*es: "API keys"', bloques and i18n)

    # La casilla existe, con aria-label y descripción, y su valor viaja en el PATCH.
    assert 't("settings.safeProxy.config.apiKeys")' in settings
    assert 'aria-label={t("settings.safeProxy.config.apiKeys.aria")}' in settings
    assert 't("settings.safeProxy.config.apiKeys.hint")' in settings
    assert "api_keys: proxyForm.api_keys" in settings
    assert "apiKeys: " not in settings  # el campo del formulario es el real (api_keys)
