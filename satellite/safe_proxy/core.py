"""Núcleo del Safe Proxy: config, estado, saneador y política (sin HTTP).

Es la **pieza compartida** por los dos modos:

- ``internal``: el interceptor del ciclo del proveedor la usa directamente (sin
  listener, sin roundtrip HTTP) para sanear lo que sale y restaurar lo que vuelve.
- ``server``: el servicio ASGI (``service.py``) la envuelve y expone la misma
  política por su API de control en loopback.

Persistencia (una sola fuente de verdad): ``<data_dir>/config.json`` (base) y
``<data_dir>/state.json`` (``enabled`` + opciones allowlisted). Nunca guarda tokens
ni secretos. La escritura es atómica y, si falla, no se aplica en memoria.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from satellite.safe_proxy.config import PATCH_KEYS, SafeProxyConfigError, validate_config_patch
from satellite.safe_proxy.defaults import DEFAULT_CONFIG
from satellite.safe_proxy.sanitizer import Blocked, Sanitizer, Vault

CONTROL_MIN_LEN = 16
STATE_VERSION = 1


class StateError(Exception):
    """El estado persistido no se puede leer o no es válido."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, obj) -> None:
    """Escritura atómica: temp en el mismo dir + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SafeProxyCore:
    """Configuración + estado + saneador del proxy, sin ninguna parte de red."""

    def __init__(
        self,
        config: dict | None = None,
        *,
        data_dir: Path | str,
        upstream_api_key: str = "",
    ) -> None:
        self.cfg = json.loads(json.dumps(config if config is not None else DEFAULT_CONFIG))
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / str(self.cfg.setdefault("server", {}).get("state_file", "state.json"))
        self.audit_path = self.data_dir / str(self.cfg["server"].get("audit_log", "audit.jsonl"))
        self.max_body = int(self.cfg["server"].get("max_body_bytes", 300_000))
        self._api_key = upstream_api_key or os.environ.get(
            str(self.cfg.get("upstream", {}).get("api_key_env", "DEEPSEEK_API_KEY")), ""
        )

        self._san = Sanitizer(self.cfg)
        self._routing_lock = threading.Lock()
        self._config_lock = threading.RLock()
        self._audit_lock = threading.Lock()
        self._routing_enabled = True

    # ------------------------------------------------------------------ #
    # Routing (habilitar/deshabilitar real)
    # ------------------------------------------------------------------ #
    def routing_enabled(self) -> bool:
        with self._routing_lock:
            return self._routing_enabled

    def set_routing(self, enabled: bool) -> None:
        with self._routing_lock:
            self._routing_enabled = bool(enabled)

    @property
    def sanitizer(self) -> Sanitizer:
        return self._san

    def _rebuild_sanitizer(self) -> None:
        self._san = Sanitizer(self.cfg)

    def api_key_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def upstream_api_key(self) -> str:
        """Clave del upstream, **sólo** para la llamada saliente (nunca se serializa)."""
        return self._api_key

    # ------------------------------------------------------------------ #
    # Transformación (lo que comparten el interceptor y el servidor)
    # ------------------------------------------------------------------ #
    def sanitize_texts(self, texts: list[str]) -> tuple[list[str], Vault]:
        """Aplica política + ofuscación + enmascarado. Lanza ``Blocked`` si viola la política."""
        vault = Vault()
        return self._san.run(texts, vault), vault

    # ------------------------------------------------------------------ #
    # Persistencia (enabled + opciones allowlisted; nunca secretos)
    # ------------------------------------------------------------------ #
    def persist_enabled(self, enabled: bool) -> None:
        with self._config_lock:
            _atomic_write_json(
                self.state_path,
                {"version": STATE_VERSION, "enabled": bool(enabled), "config": self._persist_subset()},
            )

    def _persist_subset(self) -> dict:
        out: dict = {}
        for section, keys in PATCH_KEYS.items():
            sec = self.cfg.get(section) or {}
            sub = {k: sec[k] for k in keys if k in sec}
            if sub:
                out[section] = sub
        return out

    def _read_state(self) -> dict | None:
        if not self.state_path.exists():
            return None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StateError(f"estado ilegible: {type(e).__name__}") from e
        if not isinstance(data, dict):
            raise StateError("el estado debe ser un objeto JSON")
        version = data.get("version", STATE_VERSION)
        if version != STATE_VERSION:
            raise StateError(f"version de estado no soportada: {version!r}")
        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise StateError("'enabled' debe ser booleano")
        raw_cfg = data.get("config", {})
        if not isinstance(raw_cfg, dict):
            raise StateError("'config' debe ser un objeto")
        try:
            changes = validate_config_patch(raw_cfg)
        except SafeProxyConfigError as e:
            raise StateError(f"estado inválido: {e.message}") from e
        return {"enabled": enabled, "changes": changes}

    def load_state(self) -> None:
        """Aplica el estado validado; si falta o está corrupto usa el default seguro."""
        try:
            state = self._read_state()
        except StateError as e:
            self.audit(action="state_load_error", error=type(e).__name__)
            return
        if state is None:
            return
        with self._config_lock:
            for section, values in state["changes"].items():
                self.cfg.setdefault(section, {}).update(values)
            self._rebuild_sanitizer()
        self.set_routing(state["enabled"])
        self.audit(action="state_loaded", enabled=state["enabled"])

    def apply_patch(self, changes: dict) -> None:
        """Primero persiste; si falla, no se toca el estado en memoria (sin éxito falso)."""
        with self._config_lock:
            subset = self._persist_subset()
            for section, values in changes.items():
                subset.setdefault(section, {}).update(values)
            _atomic_write_json(
                self.state_path,
                {"version": STATE_VERSION, "enabled": self.routing_enabled(), "config": subset},
            )
            for section, values in changes.items():
                self.cfg.setdefault(section, {}).update(values)
            self._rebuild_sanitizer()

    # ------------------------------------------------------------------ #
    # Auditoría (sin contenido)
    # ------------------------------------------------------------------ #
    def audit(self, **fields) -> None:
        rec = {"ts": _now(), **fields}
        try:
            with self._audit_lock, open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:  # la auditoría no debe tumbar el servicio
            pass

    # ------------------------------------------------------------------ #
    # Vistas redactadas
    # ------------------------------------------------------------------ #
    def redacted_config(self) -> dict:
        srv = self.cfg.get("server", {})
        up = self.cfg.get("upstream", {})
        return {
            "server": {
                "host": srv.get("host"),
                "port": srv.get("port"),
                "control_token_source": "embedded",
                "control_token_configured": True,
                "max_body_bytes": self.max_body,
                "state_file": str(self.state_path),
            },
            "upstream": {
                "base_url": up.get("base_url"),
                "api_key_env": up.get("api_key_env", "DEEPSEEK_API_KEY"),
                "api_key_configured": self.api_key_configured(),
                "timeout_seconds": up.get("timeout_seconds"),
            },
            "policy": dict(self.cfg.get("policy", {})),
            "code": dict(self.cfg.get("code", {})),
        }

    def status_payload(self) -> dict:
        pol = self.cfg.get("policy", {})
        up = self.cfg.get("upstream", {})
        return {
            "enabled": self.routing_enabled(),
            "control": {
                "available": True,
                "token_configured": True,
                "token_source": "embedded",
            },
            "upstream_timeout_seconds": up.get("timeout_seconds"),
            "allowed_models": list(pol.get("allowed_models") or []),
            "secrets_action": pol.get("secrets_action"),
            "allow_tools": bool(pol.get("allow_tools", False)),
        }


def new_token() -> str:
    """Token nuevo del host (≥16 caracteres)."""
    return secrets.token_urlsafe(32)


__all__ = ["SafeProxyCore", "StateError", "Blocked", "new_token", "CONTROL_MIN_LEN"]
