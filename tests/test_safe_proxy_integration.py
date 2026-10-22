"""Safe Proxy: modo **interno** (por defecto) y modo **servidor**, con el backend real.

Cubre lo que pide esta fase:

- Modo ``internal`` por defecto: **no abre ningún puerto** y aplica la política en el ciclo
  del proveedor (payload saneado al salir, restaurado al volver), sin HTTP ni roundtrip.
- El interruptor del modo interno es real: apagado ⇒ bypass claro (la petición pasa intacta).
- La configuración de Ajustes se aplica al modo interno y se persiste.
- Modo ``server`` opcional: mismo código, sólo loopback, con estado y start/stop.
- Sin doble saneado: en modo servidor el ciclo del proveedor **no** se envuelve.
- Migración suave, sin referencias a la carpeta hermana, i18n es/en y UI coherente.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.core.messages import LLMRequest, LLMResponse, Message, Role, ToolCall  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.providers.base import LLMProvider  # noqa: E402
from satellite.safe_proxy import config as sp_config  # noqa: E402
from satellite.safe_proxy.interceptor import (  # noqa: E402
    SafeProxyPolicyError,
    SafeProxyUnavailableError,
    SanitizingProvider,
    load_internal_core,
    reset_cache,
    sanitize_request,
)
from satellite.safe_proxy.sanitizer import Blocked, Vault  # noqa: E402

TOKEN = "token-local-de-prueba"
RAIZ = Path(__file__).resolve().parents[1]


class _Session:
    """Sesión de mentira: Safe Proxy no toca el runtime."""

    def __init__(self, root, home=None) -> None:
        self.root = Path(root).resolve()


class _FakeProvider(LLMProvider):
    """Proveedor de mentira: guarda la petición que le llega y responde lo que se le diga."""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.seen: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.seen.append(request)
        return LLMResponse(content=self.reply)


def _puerto_libre() -> int:
    sonda = socket.socket()
    sonda.bind(("127.0.0.1", 0))
    puerto = sonda.getsockname()[1]
    sonda.close()
    return puerto


def _se_puede_enlazar(puerto: int) -> bool:
    sonda = socket.socket()
    try:
        sonda.bind(("127.0.0.1", puerto))
        return True
    except OSError:
        return False
    finally:
        sonda.close()


def _esperar_enlazable(puerto: int, timeout: float = 3.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if _se_puede_enlazar(puerto):
            return True
        time.sleep(0.05)
    return False


def _backend_de(tmp_path) -> DesktopBackend:
    return DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=_Session,
        recents_path=tmp_path / "recents.json",
    )


def _cliente(backend: DesktopBackend) -> TestClient:
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {backend.token}"})
    return client


def _peticion(texto: str, reply: str = "ok") -> tuple[_FakeProvider, LLMRequest]:
    return _FakeProvider(reply), LLMRequest(messages=(Message(role=Role.USER, content=texto),))


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Backend aislado con SATELLITE_HOME temporal; el servicio se para al terminar."""
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    reset_cache()
    backend = _backend_de(tmp_path)
    yield _cliente(backend), backend
    asyncio.run(backend.close())  # nunca dejar el servicio escuchando
    reset_cache()


# --------------------------------------------------------------------------- #
# Modo interno (por defecto): sin listener
# --------------------------------------------------------------------------- #
def test_modo_inicial_es_interno_y_sin_listener(entorno) -> None:
    vista = entorno[0].get("/api/safe-proxy").json()

    assert vista["mode"] == "internal"
    assert vista["state"] == "internal"
    assert vista["internal_enabled"] is False
    assert vista["online"] is False  # no hay listener


def test_el_modo_interno_no_abre_ningun_puerto(entorno) -> None:
    client, _backend = entorno
    puerto = _puerto_libre()
    client.post("/api/safe-proxy/connection", json={"port": puerto})

    respuesta = client.post("/api/safe-proxy/start").json()

    assert respuesta["error_code"] == "safe_proxy_internal_mode"
    assert _se_puede_enlazar(puerto)  # nada escucha
    # Y la vista sigue diciendo que está en modo interno.
    assert client.get("/api/safe-proxy").json()["state"] == "internal"


def test_payload_saneado_al_salir_y_restaurado_al_volver(entorno) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})  # interruptor real

    proveedor, peticion = _peticion("escribe a juan.perez@acme.com", "hola <EMAIL_1>")
    envoltura = SanitizingProvider(proveedor)

    respuesta = asyncio.run(envoltura.complete(peticion))

    # Saliente: el proveedor **nunca** ve el correo; ve el marcador.
    enviado = proveedor.seen[0].messages[0].content
    assert "juan.perez@acme.com" not in enviado
    assert "<EMAIL_1>" in enviado
    # Entrante: la respuesta vuelve restaurada para el runtime.
    assert respuesta.content == "hola juan.perez@acme.com"


def test_interruptor_apagado_hace_bypass(entorno) -> None:
    client, _backend = entorno
    assert client.get("/api/safe-proxy").json()["internal_enabled"] is False

    proveedor, peticion = _peticion("escribe a juan.perez@acme.com", "hola <EMAIL_1>")
    respuesta = asyncio.run(SanitizingProvider(proveedor).complete(peticion))

    assert proveedor.seen[0].messages[0].content == "escribe a juan.perez@acme.com"  # intacta
    assert respuesta.content == "hola <EMAIL_1>"  # el runtime recibe lo que dijo el modelo


def test_politica_bloquea_y_no_envia_nada(entorno) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})

    proveedor, peticion = _peticion("password = hunter2hunter2", "no debería llegar")

    with pytest.raises(SafeProxyPolicyError):
        asyncio.run(SanitizingProvider(proveedor).complete(peticion))

    assert proveedor.seen == []  # fail-closed: no se envió nada


def test_un_error_al_cargar_la_politica_no_envia_nada() -> None:
    """Si el modo interno está activo pero el núcleo falla, no se puede hacer bypass."""

    def falla():
        raise OSError("configuración ilegible")

    proveedor, peticion = _peticion("hola")

    with pytest.raises(SafeProxyUnavailableError):
        asyncio.run(
            SanitizingProvider(proveedor, core_loader=falla).complete(peticion)
        )

    assert proveedor.seen == []  # fail-closed: no se envió nada


def test_una_config_corrupta_no_hace_bypass(entorno) -> None:
    """Una config existente pero ilegible no se sustituye en silencio por los defaults."""
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})
    ruta = sp_config.default_data_dir() / "config.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("{ esto no es json", encoding="utf-8")
    reset_cache()

    proveedor, peticion = _peticion("hola")

    with pytest.raises(SafeProxyUnavailableError):
        asyncio.run(SanitizingProvider(proveedor).complete(peticion))

    assert proveedor.seen == []  # fail-closed: no se envió nada


def test_unos_argumentos_de_tool_call_que_no_son_objeto_no_se_envian() -> None:
    """Si el saneado no devuelve un objeto, se bloquea; nunca se usan los argumentos crudos."""

    class _CoreRaro:
        class sanitizer:
            @staticmethod
            def run(texts, vault):
                return ["[]" for _ in texts]

        def sanitize_texts(self, texts):
            return list(texts), Vault()

    peticion = LLMRequest(
        messages=(
            Message(
                role=Role.ASSISTANT,
                tool_calls=(
                    ToolCall(id="c1", name="leer", arguments={"path": "secreto.txt"}),
                ),
            ),
        )
    )

    with pytest.raises(Blocked):
        sanitize_request(peticion, _CoreRaro())


def test_la_config_de_ajustes_se_aplica_al_modo_interno(entorno) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})

    aplicado = client.patch(
        "/api/safe-proxy/config",
        json={"policy": {"mask_terms": ["Acme"], "pii": ["EMAIL"]}},
    )
    assert aplicado.status_code == 200

    proveedor, peticion = _peticion("Acme y juan.perez@acme.com", "ok")
    asyncio.run(SanitizingProvider(proveedor).complete(peticion))

    enviado = proveedor.seen[0].messages[0].content
    assert "Acme" not in enviado and "juan.perez@acme.com" not in enviado
    # Y el núcleo interno lee esa misma config (una sola fuente de verdad).
    core = load_internal_core()
    assert core is not None and core.cfg["policy"]["mask_terms"] == ["Acme"]


def test_deshabilitar_no_apaga_el_proveedor(entorno) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})
    client.post("/api/safe-proxy/toggle", json={"enabled": False})

    proveedor, peticion = _peticion("password = hunter2hunter2", "sigo vivo")
    respuesta = asyncio.run(SanitizingProvider(proveedor).complete(peticion))

    assert len(proveedor.seen) == 1  # la petición llegó igual: sólo se hizo bypass
    assert respuesta.content == "sigo vivo"


def test_el_modo_interno_no_toca_los_esquemas_de_tools(entorno) -> None:
    from satellite.core.tool_models import ToolSchema

    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})

    proveedor = _FakeProvider()
    peticion = LLMRequest(
        messages=(Message(role=Role.USER, content="hola"),),
        tools=(ToolSchema(name="leer_fichero", description="Lee un fichero", parameters={}),),
    )

    asyncio.run(SanitizingProvider(proveedor).complete(peticion))

    assert proveedor.seen[0].tools[0].name == "leer_fichero"  # el nombre no se ofusca


# --------------------------------------------------------------------------- #
# Modo servidor: mismo código, sólo loopback, con start/stop
# --------------------------------------------------------------------------- #
def test_elegir_modo_servidor_lo_enciende_en_loopback(entorno) -> None:
    client, _backend = entorno
    puerto = _puerto_libre()
    client.post("/api/safe-proxy/connection", json={"port": puerto})

    vista = client.post("/api/safe-proxy/mode", json={"mode": "server"}).json()

    assert vista["mode"] == "server"
    assert vista["state"] == "running"
    assert vista["online"] is True
    assert vista["host"] == "127.0.0.1"
    assert not _se_puede_enlazar(puerto)  # escuchando


def test_volver_a_interno_apaga_el_servidor(entorno) -> None:
    client, _backend = entorno
    puerto = _puerto_libre()
    client.post("/api/safe-proxy/connection", json={"port": puerto})
    client.post("/api/safe-proxy/mode", json={"mode": "server"})

    vista = client.post("/api/safe-proxy/mode", json={"mode": "internal"}).json()

    assert vista["mode"] == "internal"
    assert vista["state"] == "internal"
    assert _esperar_enlazable(puerto)  # el puerto quedó libre


def test_puerto_ocupado_se_reporta_en_modo_servidor(entorno) -> None:
    client, _backend = entorno
    puerto = _puerto_libre()
    client.post("/api/safe-proxy/connection", json={"port": puerto})

    intruso = socket.socket()
    intruso.bind(("127.0.0.1", puerto))
    intruso.listen(1)
    try:
        vista = client.post("/api/safe-proxy/mode", json={"mode": "server"}).json()
        assert vista["state"] == "port-conflict"
        assert vista["error_code"] == "safe_proxy_port_conflict"
        assert str(puerto) in vista["error"]
    finally:
        intruso.close()


def test_encender_y_apagar_a_mano_no_deja_el_puerto_ocupado(entorno) -> None:
    client, _backend = entorno
    puerto = _puerto_libre()
    client.post("/api/safe-proxy/connection", json={"port": puerto})
    client.post("/api/safe-proxy/mode", json={"mode": "server"})
    client.post("/api/safe-proxy/stop")

    apagado = client.post("/api/safe-proxy/start").json()
    assert apagado["state"] == "running"

    client.post("/api/safe-proxy/stop")
    assert _esperar_enlazable(puerto)


def test_servidor_parado_patch_y_toggle_son_claros(entorno) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/connection", json={"port": _puerto_libre()})
    client.post("/api/safe-proxy/mode", json={"mode": "server"})
    client.post("/api/safe-proxy/stop")

    patch = client.patch("/api/safe-proxy/config", json={"upstream": {"timeout_seconds": 30}})
    toggle = client.post("/api/safe-proxy/toggle", json={"enabled": False})

    assert patch.status_code == 409
    assert patch.json()["error"]["code"] == "safe_proxy_stopped"
    assert toggle.status_code == 409


def test_routing_deshabilitado_responde_503_y_luego_vuelve(entorno, tmp_path) -> None:
    import httpx

    client, _backend = entorno
    puerto = _puerto_libre()
    client.post("/api/safe-proxy/connection", json={"port": puerto})
    client.post("/api/safe-proxy/mode", json={"mode": "server"})

    ajustes = json.loads((tmp_path / "home" / "safe_proxy.json").read_text(encoding="utf-8"))
    acceso = {"Authorization": f"Bearer {ajustes['access_token']}"}
    base = f"http://127.0.0.1:{puerto}"
    cuerpo = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hola"}]}

    assert client.post("/api/safe-proxy/toggle", json={"enabled": False}).json()["enabled"] is False
    apagado = httpx.post(f"{base}/v1/chat/completions", json=cuerpo, headers=acceso, timeout=5)
    assert apagado.status_code == 503

    client.post("/api/safe-proxy/toggle", json={"enabled": True})
    activo = httpx.post(f"{base}/v1/chat/completions", json=cuerpo, headers=acceso, timeout=5)
    assert activo.status_code != 503


def test_en_modo_servidor_el_ciclo_del_proveedor_no_sanea(entorno, tmp_path, monkeypatch) -> None:
    """Sin doble saneado: el servidor saneó, el proveedor no vuelve a hacerlo."""
    client, _backend = entorno
    client.post("/api/safe-proxy/connection", json={"port": _puerto_libre()})
    client.post("/api/safe-proxy/mode", json={"mode": "server"})
    client.post("/api/safe-proxy/toggle", json={"enabled": True})

    assert load_internal_core() is None  # el proveedor queda fuera

    proveedor, peticion = _peticion("juan.perez@acme.com", "ok")
    asyncio.run(SanitizingProvider(proveedor).complete(peticion))
    assert proveedor.seen[0].messages[0].content == "juan.perez@acme.com"  # intacta


# --------------------------------------------------------------------------- #
# Persistencia del modo y migración suave
# --------------------------------------------------------------------------- #
def test_el_modo_persiste_entre_arranques(entorno, tmp_path) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/connection", json={"port": _puerto_libre()})
    client.post("/api/safe-proxy/mode", json={"mode": "server"})
    client.post("/api/safe-proxy/stop")

    otro = _backend_de(tmp_path)
    try:
        vista = _cliente(otro).get("/api/safe-proxy").json()
        assert vista["mode"] == "server"
        assert vista["state"] == "stopped"  # no arranca solo al abrir la app
    finally:
        asyncio.run(otro.close())


def test_modo_invalido_se_rechaza(entorno) -> None:
    respuesta = entorno[0].post("/api/safe-proxy/mode", json={"mode": "sí"})

    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "safe_proxy_invalid_mode"


def test_migra_las_preferencias_previas_del_host(tmp_path) -> None:
    """El archivo de la fase anterior (``url`` + ``control_token``) se lee y no cambia nada."""
    destino = tmp_path / "safe_proxy.json"
    destino.write_text(
        json.dumps({"url": "http://127.0.0.1:9123", "control_token": "c" * 32}),
        encoding="utf-8",
    )

    ajustes = sp_config.load_settings(destino)

    assert ajustes.host == "127.0.0.1"
    assert ajustes.port == 9123  # el puerto se deriva de la URL anterior
    assert ajustes.control_token == "c" * 32
    assert ajustes.mode == "internal"  # migración suave: por defecto, interno
    assert ajustes.internal_enabled is False  # y apagado: nada sale saneado sin querer
    assert ajustes.token_set is False  # falta el token de acceso: lo genera Satellite

    generado = sp_config.ensure_settings(destino)

    assert generado.token_set is True
    assert generado.port == 9123
    assert generado.control_token == "c" * 32  # no se pisa el token existente


# --------------------------------------------------------------------------- #
# Redacción, validación y depuraciones
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        {"server": {"host": "0.0.0.0"}},
        {"policy": {"block_regex": ["x"]}},
        {"policy": {"secrets_action": "nope"}},
        {"policy": {"pii": ["EMAIL", "NOPE"]}},
        {"upstream": {"timeout_seconds": 0}},
        {"upstream": {"timeout_seconds": 999999}},
        {},
    ],
)
def test_patch_invalido_se_rechaza(entorno, payload) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})

    respuesta = client.patch("/api/safe-proxy/config", json=payload)

    assert respuesta.status_code == 422, payload
    assert respuesta.json()["error"]["code"] in {"safe_proxy_invalid", "safe_proxy_no_changes"}


@pytest.mark.parametrize(
    "payload", [{"port": 0}, {"port": 70000}, {"host": "0.0.0.0"}, {"host": "192.168.1.5"}]
)
def test_puerto_o_host_invalido_se_rechaza(entorno, payload) -> None:
    respuesta = entorno[0].post("/api/safe-proxy/connection", json=payload)

    assert respuesta.status_code == 422, payload
    assert respuesta.json()["error"]["code"] in {
        "safe_proxy_invalid_port",
        "safe_proxy_not_loopback",
    }


def test_ninguna_respuesta_filtra_los_tokens(entorno, tmp_path) -> None:
    client, _backend = entorno
    client.post("/api/safe-proxy/toggle", json={"enabled": True})
    client.post("/api/safe-proxy/connection", json={"port": _puerto_libre()})

    ajustes = json.loads((tmp_path / "home" / "safe_proxy.json").read_text(encoding="utf-8"))
    for respuesta in (
        client.get("/api/safe-proxy"),
        client.post("/api/safe-proxy/mode", json={"mode": "internal"}),
        client.post("/api/safe-proxy/toggle", json={"enabled": True}),
    ):
        assert ajustes["control_token"] not in respuesta.text
        assert ajustes["access_token"] not in respuesta.text

    vista = client.get("/api/safe-proxy").json()
    assert vista["control_token_set"] is True
    assert ajustes["control_token"] not in json.dumps(vista)


def test_no_hay_dependencia_de_la_carpeta_externa() -> None:
    prohibidos = [
        "Desktop/safe-proxy",
        "Desktop\\safe-proxy",
        "SAFE_PROXY_CONFIG",
        "python safe_proxy.py",
    ]
    fuentes = list((RAIZ / "satellite").rglob("*.py")) + list(
        (RAIZ / "desktop" / "src").rglob("*.ts*")
    )
    for archivo in fuentes:
        texto = archivo.read_text(encoding="utf-8")
        for patron in prohibidos:
            assert patron not in texto, f"{archivo}: {patron}"

    # Ningún módulo importa el `safe_proxy.py` externo como módulo raíz.
    for archivo in (RAIZ / "satellite").rglob("*.py"):
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            limpia = linea.strip()
            assert not (
                limpia.startswith("import safe_proxy") or limpia.startswith("from safe_proxy")
            ), f"{archivo}: {limpia}"

    # El servicio vive dentro del repo y no lanza procesos externos.
    base = RAIZ / "satellite" / "safe_proxy"
    for nombre in ("service.py", "core.py", "interceptor.py", "manager.py"):
        assert (base / nombre).exists(), nombre
    manager = (base / "manager.py").read_text(encoding="utf-8")
    assert "import subprocess" not in manager
    assert "subprocess." not in manager
    assert "Popen(" not in manager


def test_archivos_vendorizados_presentes() -> None:
    base = RAIZ / "satellite" / "safe_proxy"
    for nombre in (
        "service.py",
        "sanitizer.py",
        "manager.py",
        "defaults.py",
        "client.py",
        "config.yaml",
        "README.md",
        "NOTICE.md",
    ):
        assert (base / nombre).exists(), nombre
    assert "licencia" in (base / "NOTICE.md").read_text(encoding="utf-8").lower()


# --------------------------------------------------------------------------- #
# i18n es/en y UI (selector de modo, sin instrucción obsoleta)
# --------------------------------------------------------------------------- #
def test_i18n_y_ui_del_selector_de_modo() -> None:
    i18n = (RAIZ / "desktop" / "src" / "i18n.tsx").read_text(encoding="utf-8")
    settings = (RAIZ / "desktop" / "src" / "Settings.tsx").read_text(encoding="utf-8")
    app = (RAIZ / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")
    main = (RAIZ / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")

    bloques = dict(re.findall(r'"([^"]+)":\s*\{(.*?)\}\s*,\s*\n', i18n, re.S))
    for clave in (
        "cat.safeProxy",
        "cat.safeProxy.resumen",
        "settings.safeProxy.mode",
        "settings.safeProxy.mode.internal",
        "settings.safeProxy.mode.server",
        "settings.safeProxy.internal.toggle",
        "settings.safeProxy.state.internal",
        "settings.safeProxy.state.stopped",
        "settings.safeProxy.state.running",
        "settings.safeProxy.state.configError",
        "settings.safeProxy.state.portConflict",
        "settings.safeProxy.start",
        "settings.safeProxy.stop",
    ):
        assert clave in bloques, clave
        assert re.search(r"\bes:", bloques[clave]) and re.search(r"\ben:", bloques[clave]), clave

    usadas = set(re.findall(r't\("(settings\.safeProxy\.[A-Za-z0-9.]+)"', settings))
    faltan = sorted(clave for clave in usadas if clave not in bloques)
    assert usadas and not faltan, f"claves sin declarar: {faltan}"

    # La instrucción obsoleta ("lanza python safe_proxy.py") ya no existe.
    assert "settings.safeProxy.offlineCommand" not in i18n
    assert "safe_proxy.py" not in settings

    # La categoría está enlazada y Electron permite las rutas del servicio.
    assert "safe-proxy" in app
    assert '"/safe-proxy"' in main
    assert "/safe-proxy/mode" in settings
    assert "/safe-proxy/start" in settings and "/safe-proxy/stop" in settings
