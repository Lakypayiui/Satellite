"""Tolerant loader for ``~/.satellite/lsp.json``."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from satellite.kernel.paths import resolve_paths

FILE_NAME = "lsp.json"


@dataclass(frozen=True)
class ServerOverride:
    command: tuple[str, ...] = ()
    args: tuple[str, ...] = ()
    language_id: str = ""


@dataclass(frozen=True)
class LspSettings:
    auto_install: bool = True
    install_timeout_s: int = 300
    servers: dict[str, ServerOverride] = field(default_factory=dict)


def default_path() -> Path:
    return resolve_paths(None).global_root / FILE_NAME


def _strings(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        try:
            return tuple(shlex.split(raw))
        except ValueError:
            return ()
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str) and item.strip())


def _override(raw: Any) -> ServerOverride | None:
    if isinstance(raw, str):
        command = _strings(raw)
        return ServerOverride(command=command) if command else None
    if not isinstance(raw, dict):
        return None
    command = _strings(raw.get("command"))
    args = _strings(raw.get("args"))
    language_id = raw.get("language_id")
    return ServerOverride(
        command=command,
        args=args,
        language_id=str(language_id) if isinstance(language_id, str) else "",
    )


def load_lsp_settings(path: Path | None = None) -> LspSettings:
    target = Path(path) if path is not None else default_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LspSettings()
    if not isinstance(data, dict):
        return LspSettings()
    try:
        timeout = int(data.get("install_timeout_s", 300))
    except (TypeError, ValueError):
        timeout = 300
    timeout = min(1800, max(30, timeout))
    servers: dict[str, ServerOverride] = {}
    raw_servers = data.get("servers")
    if isinstance(raw_servers, dict):
        for key, value in raw_servers.items():
            override = _override(value)
            if override is not None:
                servers[str(key).strip().lower()] = override
    return LspSettings(
        auto_install=bool(data.get("auto_install", True)),
        install_timeout_s=timeout,
        servers=servers,
    )
