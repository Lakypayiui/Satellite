"""Ciclo de vida del Safe Proxy **embebido** (in-process, sin procesos externos).

El backend de escritorio es el dueño del servicio: lo arranca y lo para en el mismo proceso
Python, con ``uvicorn`` sobre un socket que **nosotros** enlazamos (así el "puerto ocupado"
se detecta antes de intentar arrancar y el puerto se libera de verdad al parar). No se
lanzan procesos externos ni se depende de ninguna carpeta hermana: el servicio vive en
``satellite/safe_proxy/`` y sus datos en ``<SATELLITE_HOME>/safe_proxy/``.

Modos:
  - ``internal`` (por defecto): **no abre ningún puerto**. La política la aplica el propio
    Satellite en el ciclo del proveedor (``interceptor.py``); aquí sólo se lee/escribe su
    configuración y sus interruptores.
  - ``server``: levanta el servicio en loopback para consumidores externos.

Estados que reporta (los mismos que pinta la pantalla):
  - ``internal``      — modo interno (sin listener); ``enabled`` es su interruptor real.
  - ``stopped``       — el servidor no está escuchando.
  - ``running``       — el servidor escucha y la API de control responde.
  - ``config-error``  — no se pudo construir/arrancar el servicio o la config es inválida.
  - ``port-conflict`` — el puerto ya está en uso por otro proceso.

Los tokens (acceso y control) son de host, **write-only**: nunca se devuelven en la vista.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace
from pathlib import Path

from satellite.safe_proxy import config
from satellite.safe_proxy.client import SafeProxyClient, SafeProxyError

START_TIMEOUT = 10.0
STOP_TIMEOUT = 10.0

STATE_INTERNAL = "internal"
STATE_STOPPED = "stopped"
STATE_RUNNING = "running"
STATE_CONFIG_ERROR = "config-error"
STATE_PORT_CONFLICT = "port-conflict"


class SafeProxyManager:
    """Arranca/para el servicio embebido y expone su estado para la interfaz."""

    def __init__(
        self,
        *,
        settings_path: Path | str | None = None,
        data_dir: Path | str | None = None,
        upstream_resolver=None,
    ) -> None:
        self._settings_path = Path(settings_path) if settings_path is not None else None
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._upstream_resolver = upstream_resolver
        self._service = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._state = STATE_STOPPED
        self._error = ""
        self._error_code = ""

    # ------------------------------------------------------------------ #
    # Consultas
    # ------------------------------------------------------------------ #
    @property
    def service(self):
        """Instancia viva del servicio (o ``None``); útil para pruebas."""
        return self._service

    @property
    def data_dir(self) -> Path:
        return self._data_dir or config.default_data_dir()

    def settings(self) -> config.SafeProxySettings:
        return config.load_settings(self._settings_path)

    def _running(self) -> bool:
        return (
            self._service is not None
            and self._server is not None
            and bool(getattr(self._server, "started", False))
            and self._thread is not None
            and self._thread.is_alive()
        )

    def _client(self, settings: config.SafeProxySettings) -> SafeProxyClient:
        return SafeProxyClient(settings.url, settings.control_token)

    def _base_view(self) -> dict:
        settings = self.settings()
        return {
            "state": self._state,
            "mode": settings.mode,
            "internal_enabled": settings.internal_enabled,
            "host": settings.host,
            "port": settings.port,
            "url": settings.url,
            "control_token_set": settings.token_set,
            "online": False,
            "enabled": None,
            "control": None,
            "state_payload": None,
            "config": None,
            "error": self._error,
            "error_code": self._error_code,
        }

    def _internal_core(self):
        """Núcleo del modo interno: la misma config/estado que el servidor, sin red."""
        from satellite.safe_proxy.core import SafeProxyCore

        core = SafeProxyCore(self._load_base_config(), data_dir=self.data_dir)
        core.load_state()
        return core

    def status(self) -> dict:
        """Vista real: modo interno (sin listener) o estado del servidor en loopback."""
        view = self._base_view()
        if self.settings().mode == config.MODE_INTERNAL:
            view["state"] = STATE_INTERNAL
            view["enabled"] = bool(self.settings().internal_enabled)
            try:
                core = self._internal_core()
                view["config"] = core.redacted_config()
                view["state_payload"] = core.status_payload()
                view["control"] = view["state_payload"].get("control")
            except Exception as exc:  # noqa: BLE001 — config ilegible: se dice, no se rompe
                view["error"] = str(exc)
                view["error_code"] = "safe_proxy_config_error"
            return view
        if not self._running():
            return view
        settings = self.settings()
        try:
            state = self._client(settings).status()
            redacted = self._client(settings).config()
        except SafeProxyError as exc:
            view["state"] = STATE_CONFIG_ERROR
            view["error"] = exc.message
            view["error_code"] = exc.code
            return view
        view["state"] = STATE_RUNNING
        view["online"] = True
        view["state_payload"] = state
        view["config"] = redacted
        view["enabled"] = bool(state.get("enabled")) if isinstance(state, dict) else None
        view["control"] = state.get("control") if isinstance(state, dict) else None
        return view

    # ------------------------------------------------------------------ #
    # Arranque y parada
    # ------------------------------------------------------------------ #
    def start(self) -> dict:
        """Arranca el servicio en loopback (idempotente). En modo interno **no** hay listener."""
        if self._running():
            return self.status()
        if self.settings().mode == config.MODE_INTERNAL:
            view = self.status()
            view["error"] = (
                "el modo interno no abre ningún puerto: la política se aplica en el ciclo "
                "del proveedor. Cambia a modo servidor para escuchar."
            )
            view["error_code"] = "safe_proxy_internal_mode"
            return view
        self._error = ""
        self._error_code = ""

        settings = config.ensure_settings(self._settings_path)
        data_dir = self.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        try:
            service = self._build_service(settings, data_dir)
        except Exception as exc:  # noqa: BLE001 — config inválida: se reporta, no se revienta
            self._state = STATE_CONFIG_ERROR
            self._error = str(exc)
            self._error_code = "safe_proxy_config_error"
            return self._base_view()
        self._service = service

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # En Windows no se usa SO_REUSEADDR: permite "robar" un puerto en uso y rompería
        # la detección de conflicto. Se enlaza en exclusiva.
        try:
            sock.bind((settings.host, settings.port))
            sock.listen(128)
        except OSError as exc:
            sock.close()
            self._service = None
            self._state = STATE_PORT_CONFLICT
            self._error = f"el puerto {settings.port} ya está en uso ({type(exc).__name__})"
            self._error_code = "safe_proxy_port_conflict"
            return self._base_view()

        try:
            import uvicorn

            uvi_config = uvicorn.Config(
                service.app, log_level="warning", access_log=False, lifespan="off"
            )
            server = uvicorn.Server(uvi_config)
            thread = threading.Thread(
                target=server.run,
                kwargs={"sockets": [sock]},
                name="safe-proxy",
                daemon=True,
            )
            thread.start()
            deadline = time.monotonic() + START_TIMEOUT
            while time.monotonic() < deadline and not server.started and thread.is_alive():
                time.sleep(0.02)
            if not server.started:
                server.should_exit = True
                thread.join(timeout=2)
                try:
                    sock.close()
                except OSError:
                    pass
                self._service = None
                self._state = STATE_CONFIG_ERROR
                self._error = "el servicio no llegó a arrancar"
                self._error_code = "safe_proxy_start_failed"
                return self._base_view()
        except Exception as exc:  # noqa: BLE001
            try:
                sock.close()
            except OSError:
                pass
            self._service = None
            self._state = STATE_CONFIG_ERROR
            self._error = str(exc)
            self._error_code = "safe_proxy_start_failed"
            return self._base_view()

        self._server = server
        self._thread = thread
        self._sock = sock
        self._state = STATE_RUNNING
        return self.status()

    def stop(self) -> dict:
        """Para el servicio embebido y libera el puerto (idempotente)."""
        server, thread, sock = self._server, self._thread, self._sock
        self._server = self._thread = self._sock = None
        self._service = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=STOP_TIMEOUT)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if server is not None or thread is not None:
            if thread is not None and thread.is_alive():
                self._state = STATE_CONFIG_ERROR
                self._error = "el servicio no se detuvo a tiempo"
                self._error_code = "safe_proxy_stop_timeout"
            else:
                self._state = STATE_STOPPED
                self._error = ""
                self._error_code = ""
        return self._base_view()

    # ------------------------------------------------------------------ #
    # Configuración (bind + opciones allowlisted)
    # ------------------------------------------------------------------ #
    def configure(self, *, host: object = None, port: object = None) -> dict:
        """Cambia host/puerto del servicio embebido; si estaba vivo, se reinicia ahí."""
        try:
            new_host = config.normalize_host(host) if host is not None else self.settings().host
            new_port = config.normalize_port(port) if port is not None else self.settings().port
        except config.SafeProxyConfigError:
            raise
        current = config.ensure_settings(self._settings_path)
        was_running = self._running()
        if was_running:
            self.stop()
        # El modo y su interruptor se conservan: cambiar el puerto no cambia de modo.
        config.write_settings(
            replace(current, host=new_host, port=new_port),
            self._settings_path,
        )
        if was_running:
            return self.start()
        self._state = STATE_STOPPED
        self._error = ""
        self._error_code = ""
        return self._base_view()

    def set_mode(self, mode: object) -> dict:
        """Cambia de modo: ``internal`` (sin listener) o ``server`` (loopback, se enciende)."""
        limpio = str(mode or "").strip().lower()
        if limpio not in config.MODES:
            raise config.SafeProxyConfigError(
                "safe_proxy_invalid_mode", "el modo debe ser 'internal' o 'server'"
            )
        actual = config.ensure_settings(self._settings_path)
        if actual.mode == limpio:
            return self.status()
        self._update_settings(mode=limpio)
        if limpio == config.MODE_INTERNAL:
            self.stop()  # el modo interno no deja nada escuchando
            return self.status()
        # Selección explícita del modo servidor: se enciende (y si el puerto está ocupado,
        # se reporta el conflicto sin romper nada).
        return self.start()

    def set_internal_enabled(self, enabled: bool) -> dict:
        """Interruptor real del modo interno: con él apagado, la petición hace bypass."""
        self._update_settings(internal_enabled=bool(enabled))
        return self.status()

    def patch(self, changes: dict) -> dict:
        """Aplica la allowlist: en modo interno al núcleo; en servidor, al servicio vivo."""
        settings = self.settings()
        if settings.mode == config.MODE_SERVER:
            if not self._running():
                raise SafeProxyError(
                    "safe_proxy_stopped", "el servicio está parado; arráncalo para configurarlo", 409
                )
            self._client(settings).patch_config(changes)
            return self.status()
        self._internal_core().apply_patch(changes)
        return self.status()

    def set_enabled(self, enabled: bool) -> dict:
        """Enciende/apaga de verdad: routing del servidor, o el interruptor interno."""
        settings = self.settings()
        if settings.mode == config.MODE_SERVER:
            if not self._running():
                raise SafeProxyError(
                    "safe_proxy_stopped", "el servicio está parado; arráncalo para cambiarlo", 409
                )
            self._client(settings).set_enabled(bool(enabled))
            return self.status()
        return self.set_internal_enabled(bool(enabled))

    def _update_settings(self, **changes) -> config.SafeProxySettings:
        """Lee-modifica-escribe la configuración del host conservando lo demás."""
        current = config.ensure_settings(self._settings_path)
        config.write_settings(replace(current, **changes), self._settings_path)
        return config.load_settings(self._settings_path)

    # ------------------------------------------------------------------ #
    # Construcción del servicio
    # ------------------------------------------------------------------ #
    def _load_base_config(self) -> dict:
        """Config base del servicio desde ``<data_dir>/config.json`` (o los defaults)."""
        import json

        from satellite.safe_proxy.defaults import DEFAULT_CONFIG

        path = self.data_dir / "config.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    return data
            except (OSError, ValueError):
                pass
        # Primera vez: se deja una copia editable junto al estado/auditoría.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
        return json.loads(json.dumps(DEFAULT_CONFIG))

    def _build_service(self, settings: config.SafeProxySettings, data_dir: Path):
        from satellite.safe_proxy.service import SafeProxyService

        cfg = self._load_base_config()
        upstream = self._resolve_upstream()
        if isinstance(upstream, dict) and upstream.get("base_url"):
            cfg.setdefault("upstream", {})["base_url"] = upstream["base_url"]
        cfg.setdefault("server", {})["port"] = settings.port
        return SafeProxyService(
            cfg,
            access_token=settings.access_token,
            control_token=settings.control_token,
            data_dir=data_dir,
            upstream_api_key=(upstream or {}).get("api_key", "") if isinstance(upstream, dict) else "",
        )

    def _resolve_upstream(self) -> dict | None:
        """Credenciales del upstream (base_url + api_key) vía el resolver inyectado."""
        if self._upstream_resolver is None:
            return None
        try:
            return self._upstream_resolver() or None
        except Exception:  # noqa: BLE001 — sin proveedor se usa el default y se dice aparte
            return None
