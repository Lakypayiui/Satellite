"""Configuración local del host para el Safe Proxy embebido.

Se lee de ``<SATELLITE_HOME>/safe_proxy.json`` (por defecto ``~/.satellite/safe_proxy.json``):
el host/puerto loopback donde escucha el servicio embebido y sus **dos** tokens, el de
acceso (``PROXY_TOKEN``) y el de control (``PROXY_CONTROL_TOKEN``). Los tokens son
**write-only**: los genera Satellite en el host, nunca se devuelven a la interfaz ni se
registran en logs.

Este módulo es también la **allowlist** compartida: la validación del ``PATCH`` que usa el
propio servicio (``validate_config_patch``) y la que aplica el backend antes de hablar con él.

Red: sólo loopback (``localhost``, ``127.0.0.0/8``, ``::1``), puerto válido, sin remotos.
"""

from __future__ import annotations

import ipaddress
import json
import secrets
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from satellite.core.atomic import atomic_write_text
from satellite.kernel.paths import resolve_paths

FILE_NAME = "safe_proxy.json"
DATA_DIR_NAME = "safe_proxy"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
TOKEN_MIN_LEN = 16
# Modos: ``internal`` (lo aplica el propio Satellite en el ciclo del proveedor, sin
# listener) y ``server`` (servicio en loopback para consumidores externos).
MODE_INTERNAL = "internal"
MODE_SERVER = "server"
MODES = (MODE_INTERNAL, MODE_SERVER)
DEFAULT_MODE = MODE_INTERNAL

# Allowlist del PATCH: secciones y claves que el servicio acepta. Cualquier otra ⇒ error.
PATCH_KEYS: dict[str, set[str]] = {
    "policy": {
        "allowed_models",
        "block_terms",
        "mask_terms",
        "pii",
        "secrets_action",
        # Familia de claves de API: se detectan (y se bloquean o enmascaran, según
        # ``secrets_action``) salvo que se apague explícitamente.
        "api_keys",
    },
    "code": {"obfuscate_python", "strip_comments", "other_languages", "fail_closed"},
    "upstream": {"timeout_seconds"},
}
PII_VALUES = ("EMAIL", "PHONE", "CURP", "RFC", "CLABE", "CARD", "IP")
SECRETS_ACTIONS = ("block", "redact")
OTHER_LANGUAGES = ("block", "allow")
TIMEOUT_MIN, TIMEOUT_MAX = 1, 3600


class SafeProxyConfigError(ValueError):
    """Error de configuración con código y mensaje legibles."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Loopback y URL
# --------------------------------------------------------------------------- #
def is_loopback_host(host: object) -> bool:
    h = str(host or "").strip().strip("[]").lower()
    if h in ("localhost", "::1", "0:0:0:0:0:0:0:1"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def normalize_host(raw: object) -> str:
    """Host loopback validado; cualquier otra cosa es un error (sin remotos)."""
    text = str(raw or "").strip().strip("[]")
    if not text:
        return DEFAULT_HOST
    if not is_loopback_host(text):
        raise SafeProxyConfigError(
            "safe_proxy_not_loopback",
            "por seguridad el servicio sólo escucha en local (localhost, 127.0.0.1 o ::1)",
        )
    return text.lower()


def normalize_port(raw: object) -> int:
    """Puerto TCP válido (1-65535)."""
    if isinstance(raw, bool):
        raise SafeProxyConfigError("safe_proxy_invalid_port", "el puerto debe ser un número")
    try:
        port = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SafeProxyConfigError("safe_proxy_invalid_port", "el puerto debe ser un número") from exc
    if not (1 <= port <= 65535):
        raise SafeProxyConfigError("safe_proxy_invalid_port", "el puerto debe estar entre 1 y 65535")
    return port


def normalize_base_url(raw: object) -> str:
    """Valida y normaliza una URL del proxy (loopback + puerto válido).

    Acepta ``http://127.0.0.1:8787``, ``127.0.0.1:8787`` o ``localhost`` (puerto por
    defecto 8787). Rechaza cualquier host que no sea loopback, esquemas distintos de
    http/https, credenciales embebidas, rutas, query o fragmento.
    """
    text = str(raw or "").strip()
    if not text:
        raise SafeProxyConfigError("safe_proxy_invalid_url", "la URL del proxy está vacía")
    if "://" not in text:
        text = f"http://{text}"
    partes = urlsplit(text)
    if partes.scheme not in ("http", "https"):
        raise SafeProxyConfigError(
            "safe_proxy_invalid_url", "la URL debe empezar por http:// o https://"
        )
    if partes.username or partes.password:
        raise SafeProxyConfigError(
            "safe_proxy_invalid_url", "la URL no admite credenciales embebidas"
        )
    host = normalize_host(partes.hostname or "")
    try:
        port = partes.port
    except ValueError as exc:
        raise SafeProxyConfigError("safe_proxy_invalid_port", "el puerto de la URL no es válido") from exc
    if port is None:
        port = DEFAULT_PORT
    port = normalize_port(port)
    if partes.path not in ("", "/") or partes.query or partes.fragment:
        raise SafeProxyConfigError(
            "safe_proxy_invalid_url", "la URL no debe incluir ruta, query ni fragmento"
        )
    host_out = f"[{host}]" if ":" in host else host
    return f"{partes.scheme}://{host_out}:{port}"


# --------------------------------------------------------------------------- #
# Validación del PATCH (allowlist real; la usan el servicio y el backend)
# --------------------------------------------------------------------------- #
def _str_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise SafeProxyConfigError("safe_proxy_invalid", f"{field} debe ser una lista de cadenas")
    if any(not x.strip() for x in value):
        raise SafeProxyConfigError("safe_proxy_invalid", f"{field} no admite elementos vacíos")
    return list(value)


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise SafeProxyConfigError("safe_proxy_invalid", f"{field} debe ser booleano")
    return value


def validate_config_patch(body: object) -> dict:
    """Normaliza y valida un PATCH; devuelve sólo los cambios válidos (``{}`` si no hay).

    Lanza :class:`SafeProxyConfigError` si algo está fuera de la allowlist o con tipos
    incorrectos, igual que el propio servicio.
    """
    if not isinstance(body, dict):
        raise SafeProxyConfigError("safe_proxy_invalid", "se espera un objeto JSON")
    if not body:
        return {}
    unknown = set(body) - set(PATCH_KEYS)
    if unknown:
        raise SafeProxyConfigError(
            "safe_proxy_invalid", f"secciones no permitidas: {sorted(unknown)}"
        )
    changes: dict = {}

    if "policy" in body:
        sec = body["policy"]
        if not isinstance(sec, dict):
            raise SafeProxyConfigError("safe_proxy_invalid", "policy debe ser un objeto")
        bad = set(sec) - PATCH_KEYS["policy"]
        if bad:
            raise SafeProxyConfigError(
                "safe_proxy_invalid", f"claves no permitidas en policy: {sorted(bad)}"
            )
        out: dict = {}
        for key in ("allowed_models", "block_terms", "mask_terms"):
            if key in sec:
                out[key] = _str_list(sec[key], f"policy.{key}")
        if "pii" in sec:
            norm = [str(v).upper() for v in _str_list(sec["pii"], "policy.pii")]
            bad_values = sorted({v for v in norm if v not in PII_VALUES})
            if bad_values:
                raise SafeProxyConfigError(
                    "safe_proxy_invalid", f"valores PII no permitidos: {bad_values}"
                )
            out["pii"] = sorted(set(norm))
        if "secrets_action" in sec:
            if sec["secrets_action"] not in SECRETS_ACTIONS:
                raise SafeProxyConfigError(
                    "safe_proxy_invalid", "policy.secrets_action debe ser 'block' o 'redact'"
                )
            out["secrets_action"] = sec["secrets_action"]
        if "api_keys" in sec:
            out["api_keys"] = _bool(sec["api_keys"], "policy.api_keys")
        if out:
            changes["policy"] = out

    if "code" in body:
        sec = body["code"]
        if not isinstance(sec, dict):
            raise SafeProxyConfigError("safe_proxy_invalid", "code debe ser un objeto")
        bad = set(sec) - PATCH_KEYS["code"]
        if bad:
            raise SafeProxyConfigError(
                "safe_proxy_invalid", f"claves no permitidas en code: {sorted(bad)}"
            )
        out = {}
        for key in ("obfuscate_python", "strip_comments", "fail_closed"):
            if key in sec:
                out[key] = _bool(sec[key], f"code.{key}")
        if "other_languages" in sec:
            if sec["other_languages"] not in OTHER_LANGUAGES:
                raise SafeProxyConfigError(
                    "safe_proxy_invalid", "code.other_languages debe ser 'block' o 'allow'"
                )
            out["other_languages"] = sec["other_languages"]
        if out:
            changes["code"] = out

    if "upstream" in body:
        sec = body["upstream"]
        if not isinstance(sec, dict):
            raise SafeProxyConfigError("safe_proxy_invalid", "upstream debe ser un objeto")
        bad = set(sec) - PATCH_KEYS["upstream"]
        if bad:
            raise SafeProxyConfigError(
                "safe_proxy_invalid", f"claves no permitidas en upstream: {sorted(bad)}"
            )
        out = {}
        if "timeout_seconds" in sec:
            value = sec["timeout_seconds"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SafeProxyConfigError(
                    "safe_proxy_invalid", "upstream.timeout_seconds debe ser numérico"
                )
            if not (TIMEOUT_MIN <= value <= TIMEOUT_MAX):
                raise SafeProxyConfigError(
                    "safe_proxy_invalid",
                    f"upstream.timeout_seconds debe estar entre {TIMEOUT_MIN} y {TIMEOUT_MAX}",
                )
            out["timeout_seconds"] = int(value) if float(value).is_integer() else float(value)
        if out:
            changes["upstream"] = out

    return changes


# --------------------------------------------------------------------------- #
# Configuración del host (host/puerto + tokens write-only)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SafeProxySettings:
    """Modo, dónde escucha el servicio y con qué tokens (nunca se devuelven).

    ``mode`` elige entre el modo **interno** (por defecto: el saneado se aplica en el ciclo
    del proveedor, sin abrir ningún puerto) y el modo **servidor** (loopback, para
    consumidores externos). ``internal_enabled`` es el interruptor real del modo interno:
    por defecto **apagado**, porque la política por defecto del proxy bloquea contenido
    habitual (bloques de código que no son Python, «All rights reserved»…) y activarlo sin
    querer rompería las peticiones del agente.
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    mode: str = DEFAULT_MODE
    internal_enabled: bool = False
    control_token: str = ""
    access_token: str = ""

    @property
    def url(self) -> str:
        host_out = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host_out}:{self.port}"

    @property
    def is_server(self) -> bool:
        return self.mode == MODE_SERVER

    @property
    def token_set(self) -> bool:
        return bool(self.control_token) and bool(self.access_token)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "mode": self.mode,
            "internal_enabled": self.internal_enabled,
            "url": self.url,
            "control_token": self.control_token,
            "access_token": self.access_token,
        }

    @classmethod
    def from_dict(cls, data: object) -> "SafeProxySettings":
        """Lectura tolerante y con migración del formato previo (``url`` + ``control_token``)."""
        if not isinstance(data, dict):
            return cls()
        host = str(data.get("host") or "").strip()
        raw_port = data.get("port")
        port = raw_port if isinstance(raw_port, int) and not isinstance(raw_port, bool) else None
        legacy_url = str(data.get("url") or "").strip()
        if (not host or port is None) and legacy_url:
            try:
                partes = urlsplit(legacy_url if "://" in legacy_url else f"http://{legacy_url}")
                if not host and partes.hostname:
                    host = partes.hostname
                if port is None:
                    port = partes.port
            except ValueError:
                pass
        if not host or not is_loopback_host(host):
            host = DEFAULT_HOST
        if port is None or not (1 <= port <= 65535):
            port = DEFAULT_PORT
        mode = str(data.get("mode") or "").strip().lower()
        if mode not in MODES:
            # Migración suave: un archivo de la fase "sólo servidor" no trae modo ⇒ interno,
            # y el interruptor interno queda **apagado** (nada cambia en las peticiones).
            mode = DEFAULT_MODE
        return cls(
            host=host.lower(),
            port=port,
            mode=mode,
            internal_enabled=bool(data.get("internal_enabled", False)),
            control_token=str(data.get("control_token") or ""),
            access_token=str(data.get("access_token") or ""),
        )


def default_path() -> Path:
    """Ruta del archivo local del host."""
    return Path(resolve_paths(None).global_root) / FILE_NAME


def default_data_dir() -> Path:
    """Carpeta de datos del servicio embebido (config/estado/auditoría)."""
    return Path(resolve_paths(None).global_root) / DATA_DIR_NAME


def load_settings(path: Path | str | None = None) -> SafeProxySettings:
    """Carga tolerante (nunca lanza): sin archivo o corrupto ⇒ defaults."""
    target = Path(path) if path is not None else default_path()
    if not target.exists():
        return SafeProxySettings()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SafeProxySettings()
    return SafeProxySettings.from_dict(data)


def write_settings(settings: SafeProxySettings, path: Path | str | None = None) -> Path:
    """Escribe el archivo local del host de forma atómica y con permisos restringidos.

    Sólo lo usa la pantalla de Ajustes del escritorio (humano). Los tokens nunca se
    registran en logs: aquí sólo se vuelcan al disco del propio usuario.
    """
    if not isinstance(settings, SafeProxySettings):
        raise ValueError("write_settings espera SafeProxySettings")
    target = Path(path) if path is not None else default_path()
    atomic_write_text(
        target,
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        mode=0o600,
    )
    return target


def ensure_settings(path: Path | str | None = None) -> SafeProxySettings:
    """Configuración del host generando (una sola vez) los tokens que falten."""
    current = load_settings(path)
    if current.token_set:
        return current
    updated = replace(
        current,
        control_token=current.control_token or secrets.token_urlsafe(32),
        access_token=current.access_token or secrets.token_urlsafe(32),
    )
    write_settings(updated, path)
    return updated
