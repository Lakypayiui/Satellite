"""Políticas de aprobación y cuotas del runtime (deterministas, no prompt)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PolicyMode(Enum):
    """Modo de aprobación global."""

    ASK = "ask"
    AUTO = "auto"
    UNRESTRICTED = "unrestricted"


@dataclass(frozen=True)
class Quotas:
    """Límites anti-abuso configurables (protegen contra spam de propuestas)."""

    max_agents: int = 50
    max_skills: int = 200
    proposals_per_hour: int = 20
    proposals_per_task: int = 5
    max_prompt_chars: int = 8000
    max_script_bytes: int = 200_000
    max_catalog_bytes: int = 5_000_000

    def to_dict(self) -> dict:
        return {
            "max_agents": self.max_agents,
            "max_skills": self.max_skills,
            "proposals_per_hour": self.proposals_per_hour,
            "proposals_per_task": self.proposals_per_task,
            "max_prompt_chars": self.max_prompt_chars,
            "max_script_bytes": self.max_script_bytes,
            "max_catalog_bytes": self.max_catalog_bytes,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "Quotas":
        if not isinstance(d, dict):
            return cls()
        defaults = cls()
        return cls(
            max_agents=int(d.get("max_agents", defaults.max_agents)),
            max_skills=int(d.get("max_skills", defaults.max_skills)),
            proposals_per_hour=int(
                d.get("proposals_per_hour", defaults.proposals_per_hour)
            ),
            proposals_per_task=int(
                d.get("proposals_per_task", defaults.proposals_per_task)
            ),
            max_prompt_chars=int(d.get("max_prompt_chars", defaults.max_prompt_chars)),
            max_script_bytes=int(d.get("max_script_bytes", defaults.max_script_bytes)),
            max_catalog_bytes=int(
                d.get("max_catalog_bytes", defaults.max_catalog_bytes)
            ),
        )


@dataclass(frozen=True)
class CompactionConfig:
    """Reglas de compactación de contexto (``llm.compaction`` en config.json).

    ``window`` es la ventana del modelo en tokens: ``None`` = detectarla por
    modelo/proveedor. ``compact_at`` es el umbral (0.85 = 85 %).
    """

    window: int | None = None
    compact_at: float = 0.85
    keep: int = 6

    def to_dict(self) -> dict:
        return {
            "window": self.window,
            "compact_at": self.compact_at,
            "keep": self.keep,
        }


@dataclass(frozen=True)
class Settings:
    """Configuración del kernel leída de ``config.json`` (con valores por defecto)."""

    policy_mode: PolicyMode = PolicyMode.ASK
    quotas: Quotas = field(default_factory=Quotas)
    sandbox_level: str = "N0"
    # Comandos que no se preguntan (``terminal.allowed_commands`` en config.json). Vacío
    # por defecto: todo comando se pregunta. No hay forma de decir "permitir todo".
    allowed_commands: tuple[str, ...] = ()
    refine_goal: bool = False
    direct_reply: bool = True
    max_tokens: dict = field(default_factory=dict)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)

    def max_tokens_for(self, role: str) -> int | None:
        """Límite de salida por rol (``llm.max_tokens.<rol>``); ``*`` = general."""
        value = self.max_tokens.get(str(role))
        if value is None:
            value = self.max_tokens.get("*")
        return int(value) if value else None

    def to_dict(self) -> dict:
        return {
            "policy_mode": self.policy_mode.value,
            "quotas": self.quotas.to_dict(),
            "sandbox_level": self.sandbox_level,
            "allowed_commands": list(self.allowed_commands),
            "refine_goal": self.refine_goal,
            "direct_reply": self.direct_reply,
            "max_tokens": dict(self.max_tokens),
            "compaction": self.compaction.to_dict(),
        }


def _compaction_from(data: dict) -> CompactionConfig:
    """Lee ``llm.compaction`` (tolerante). Ojo: ``llm.context`` es un ROL."""
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    raw = llm.get("compaction")
    if raw is None:
        raw = data.get("compaction")
    if not isinstance(raw, dict):
        return CompactionConfig()
    window: int | None = None
    try:
        value = int(raw.get("window", 0) or 0)
        window = value if value > 0 else None
    except (TypeError, ValueError):
        window = None
    try:
        compact_at = float(raw.get("compact_at", 0.85))
    except (TypeError, ValueError):
        compact_at = 0.85
    compact_at = min(1.0, max(0.1, compact_at))
    try:
        keep = int(raw.get("keep", 6))
    except (TypeError, ValueError):
        keep = 6
    return CompactionConfig(window=window, compact_at=compact_at, keep=max(2, keep))


def _max_tokens_from(data: dict) -> dict:
    """Lee ``llm.max_tokens`` (dict por rol) o ``max_tokens`` (entero general)."""
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    raw = llm.get("max_tokens", data.get("max_tokens"))
    by_role: dict[str, int] = {}
    if isinstance(raw, dict):
        for role, value in raw.items():
            if isinstance(value, bool):
                continue
            try:
                by_role[str(role)] = int(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
        by_role["*"] = int(raw)
    return by_role


def _allowed_commands_from(data: dict) -> tuple[str, ...]:
    """Lee ``terminal.allowed_commands`` (tolerante).

    Sólo nombres de binario, sin repetidos. Si el valor es basura se ignora, y el efecto de
    no leerlo es que ese comando **se pregunta**: el lado seguro del fallo.
    """
    terminal = data.get("terminal") if isinstance(data.get("terminal"), dict) else {}
    raw = terminal.get("allowed_commands")
    if not isinstance(raw, (list, tuple)):
        return ()
    vistos: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        nombre = item.strip()
        if nombre and nombre not in vistos:
            vistos.append(nombre)
    return tuple(vistos)


def load_settings(path: Path | None = None) -> Settings:
    """Carga la configuración de forma tolerante (nunca lanza por datos corruptos).

    Si el archivo no existe o es inválido, se devuelven los valores por defecto
    (``ask``). Los campos desconocidos se ignoran.
    """
    if path is None or not Path(path).exists():
        return Settings()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()

    mode_raw = str(data.get("policy_mode", data.get("policy", "ask"))).lower()
    try:
        mode = PolicyMode(mode_raw)
    except ValueError:
        mode = PolicyMode.ASK

    return Settings(
        policy_mode=mode,
        quotas=Quotas.from_dict(data.get("quotas")),
        sandbox_level=str(data.get("sandbox_level", "N0")),
        allowed_commands=_allowed_commands_from(data),
        refine_goal=bool(data.get("refine_goal", False)),
        direct_reply=bool(data.get("direct_reply", True)),
        max_tokens=_max_tokens_from(data),
        compaction=_compaction_from(data),
    )
