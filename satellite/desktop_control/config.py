"""Configuración **local del host** para control de escritorio (F4).

Se lee de ``<SATELLITE_HOME>/desktop_control.json`` (por defecto
``~/.satellite/desktop_control.json``). Es configuración del host: **nunca** la
escriben tools, la API ni MCP, y no se acepta por argumentos.

Deny-by-default: ``enabled=false`` y ``allowed_processes=[]`` (nada permitido)
hasta que el humano edite el archivo a mano. La denylist gana siempre.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from satellite.core.atomic import atomic_write_text
from satellite.kernel.paths import resolve_paths

FILE_NAME = "desktop_control.json"

# Apps vetadas por defecto: gestores de contraseñas, terminales y configuración del
# sistema. La comparación es exacta contra el nombre de proceso normalizado.
DEFAULT_DENIED_APPS: tuple[str, ...] = (
    "1password",
    "bitwarden",
    "dashlane",
    "keepass",
    "keepassxc",
    "lastpass",
    "cmd",
    "powershell",
    "pwsh",
    "windowsterminal",
    "wt",
    "conhost",
    "bash",
    "wsl",
    "regedit",
    "mmc",
    "control",
    "systemsettings",
    "taskmgr",
)


def normalize_app(value: object) -> str:
    """Nombre de proceso normalizado (sin ruta, sin ``.exe``, minúsculas)."""
    raw = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    raw = raw.strip().lower()
    if raw.endswith(".exe"):
        raw = raw[:-4]
    return " ".join(raw.split())


def _app_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    seen: list[str] = []
    for item in values:
        name = normalize_app(item)
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _positive_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class DesktopControlSettings:
    """Límites y allow/deny del host (deny-by-default)."""

    enabled: bool = False
    allowed_processes: tuple[str, ...] = ()
    denied_apps: tuple[str, ...] = DEFAULT_DENIED_APPS
    max_actions_per_run: int = 20
    max_seconds_per_run: float = 120.0
    rate_per_minute: int = 30
    confirm_destructive: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled debe ser bool")
        for value in (self.max_actions_per_run, self.rate_per_minute):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("los topes deben ser enteros >= 1")
        if not isinstance(self.max_seconds_per_run, (int, float)) or self.max_seconds_per_run <= 0:
            raise ValueError("max_seconds_per_run debe ser > 0")

    def allows_app(self, app: object) -> bool:
        """True sólo si la app está en la allowlist local y no en la denylist."""
        name = normalize_app(app)
        if not name:
            return False
        if name in self.denied_apps:
            return False
        return name in self.allowed_processes

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "allowed_processes": list(self.allowed_processes),
            "denied_apps": list(self.denied_apps),
            "max_actions_per_run": self.max_actions_per_run,
            "max_seconds_per_run": self.max_seconds_per_run,
            "rate_per_minute": self.rate_per_minute,
            "confirm_destructive": self.confirm_destructive,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "DesktopControlSettings":
        """Lectura tolerante: basura ⇒ defaults (deny-by-default)."""
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        denied = data.get("denied_apps")
        return cls(
            enabled=bool(data.get("enabled", False)),
            allowed_processes=_app_tuple(data.get("allowed_processes")),
            denied_apps=(
                _app_tuple(denied) if isinstance(denied, (list, tuple)) else defaults.denied_apps
            ),
            max_actions_per_run=_positive_int(
                data.get("max_actions_per_run"), defaults.max_actions_per_run
            ),
            max_seconds_per_run=_positive_float(
                data.get("max_seconds_per_run"), defaults.max_seconds_per_run
            ),
            rate_per_minute=_positive_int(data.get("rate_per_minute"), defaults.rate_per_minute),
            confirm_destructive=bool(data.get("confirm_destructive", True)),
        )


def default_path() -> Path:
    """Ruta del archivo local del host."""
    return Path(resolve_paths(None).global_root) / FILE_NAME


def load_desktop_settings(path: Path | str | None = None) -> DesktopControlSettings:
    """Carga tolerante (nunca lanza). Sin archivo ⇒ defaults denegados."""
    target = Path(path) if path is not None else default_path()
    if not target.exists():
        return DesktopControlSettings()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DesktopControlSettings()
    return DesktopControlSettings.from_dict(data)


def write_desktop_settings(
    settings: DesktopControlSettings,
    path: Path | str | None = None,
) -> Path:
    """Escribe el archivo local del host (atómico).

    Lo usa **sólo** la pantalla de Ajustes del escritorio (humano). Las tools, los
    agentes y MCP no tienen acceso a esta ruta ni a este escritor.
    """
    if not isinstance(settings, DesktopControlSettings):
        raise ValueError("write_desktop_settings espera DesktopControlSettings")
    target = Path(path) if path is not None else default_path()
    atomic_write_text(
        target, json.dumps(settings.to_dict(), ensure_ascii=False, indent=2)
    )
    return target
