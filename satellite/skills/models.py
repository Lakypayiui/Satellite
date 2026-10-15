"""Modelos de skill: manifest, estado y estadísticas (contratos puros)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from satellite.core.errors import ContractError, SpecValidationError
from satellite.core.spec import ALLOWED_LIFECYCLES

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

ALLOWED_LANGUAGES: tuple[str, ...] = ("python", "bash")


class SkillStatus(Enum):
    """Ciclo de vida de una skill."""

    ACTIVE = "active"
    QUARANTINED = "quarantined"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class SkillStats:
    """Métricas de uso de una skill (la línea base es una estimación)."""

    uses: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    creation_tokens: int = 0
    first_resolution_tokens: int = 0
    total_latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "uses": self.uses,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "creation_tokens": self.creation_tokens,
            "first_resolution_tokens": self.first_resolution_tokens,
            "total_latency_ms": self.total_latency_ms,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "SkillStats":
        if not isinstance(d, dict):
            return cls()
        defaults = cls()
        return cls(
            uses=int(d.get("uses", defaults.uses)),
            successes=int(d.get("successes", defaults.successes)),
            failures=int(d.get("failures", defaults.failures)),
            consecutive_failures=int(
                d.get("consecutive_failures", defaults.consecutive_failures)
            ),
            creation_tokens=int(d.get("creation_tokens", defaults.creation_tokens)),
            first_resolution_tokens=int(
                d.get("first_resolution_tokens", defaults.first_resolution_tokens)
            ),
            total_latency_ms=int(d.get("total_latency_ms", defaults.total_latency_ms)),
        )


@dataclass(frozen=True)
class SkillManifest:
    """Descriptor declarativo de una skill (datos, no código importable)."""

    id: str
    version: str = "1.0.0"
    description: str = ""
    tags: tuple[str, ...] = ()
    language: str = "python"
    entrypoint: str = "main.py"
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    examples: tuple[str, ...] = ()
    hash: str = ""
    status: SkillStatus = SkillStatus.ACTIVE
    lifecycle: str = "persistent"
    deterministic: bool = False
    stats: SkillStats = field(default_factory=SkillStats)
    created_by: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_RE.match(self.id):
            raise SpecValidationError(
                f"SkillManifest: id debe ser snake_case (^[a-z][a-z0-9_]*$), "
                f"recibido {self.id!r}"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise SpecValidationError(
                f"SkillManifest '{self.id}': description no puede estar vacía"
            )
        if not isinstance(self.language, str) or not self.language.strip():
            raise SpecValidationError(
                f"SkillManifest '{self.id}': language no puede estar vacio"
            )
        if not isinstance(self.entrypoint, str) or not self.entrypoint.strip():
            raise SpecValidationError(
                f"SkillManifest '{self.id}': entrypoint no puede estar vacío"
            )
        if (
            "/" in self.entrypoint
            or "\\" in self.entrypoint
            or ".." in self.entrypoint
            or self.entrypoint != self.entrypoint.strip()
        ):
            raise SpecValidationError(
                f"SkillManifest '{self.id}': entrypoint debe ser un nombre de "
                "archivo simple (sin rutas ni '..')"
            )
        if not isinstance(self.status, SkillStatus):
            raise ContractError(
                f"SkillManifest '{self.id}': status debe ser SkillStatus"
            )
        if self.lifecycle not in ALLOWED_LIFECYCLES:
            raise SpecValidationError(
                f"SkillManifest '{self.id}': lifecycle debe ser uno de "
                f"{', '.join(ALLOWED_LIFECYCLES)} (recibido {self.lifecycle!r})"
            )
        if not isinstance(self.stats, SkillStats):
            raise ContractError(f"SkillManifest '{self.id}': stats debe ser SkillStats")
        if not self.created_at:
            object.__setattr__(
                self, "created_at", datetime.now(timezone.utc).isoformat()
            )

    def with_stats(self, stats: SkillStats) -> "SkillManifest":
        """Devuelve una copia con nuevas estadísticas."""
        data = self.to_dict()
        data["stats"] = stats.to_dict()
        return SkillManifest.from_dict(data)

    def with_status(self, status: SkillStatus) -> "SkillManifest":
        """Devuelve una copia con nuevo estado."""
        data = self.to_dict()
        data["status"] = status.value
        return SkillManifest.from_dict(data)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "tags": list(self.tags),
            "language": self.language,
            "entrypoint": self.entrypoint,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "permissions": dict(self.permissions),
            "examples": list(self.examples),
            "hash": self.hash,
            "status": self.status.value,
            "lifecycle": self.lifecycle,
            "deterministic": bool(self.deterministic),
            "stats": self.stats.to_dict(),
            "created_by": self.created_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillManifest":
        if not isinstance(d, dict):
            raise SpecValidationError("SkillManifest.from_dict: se esperaba dict")
        raw_status = d.get("status", SkillStatus.ACTIVE.value)
        status = (
            raw_status if isinstance(raw_status, SkillStatus) else SkillStatus(raw_status)
        )
        return cls(
            id=d["id"],
            version=d.get("version", "1.0.0"),
            description=d.get("description", ""),
            tags=tuple(str(t) for t in (d.get("tags") or [])),
            language=d.get("language", "python"),
            entrypoint=d.get("entrypoint", "main.py"),
            inputs=dict(d.get("inputs") or {}),
            outputs=dict(d.get("outputs") or {}),
            permissions=dict(d.get("permissions") or {}),
            examples=tuple(str(e) for e in (d.get("examples") or [])),
            hash=d.get("hash", ""),
            status=status,
            lifecycle=str(d.get("lifecycle", "persistent")),
            deterministic=bool(d.get("deterministic", False)),
            stats=SkillStats.from_dict(d.get("stats")),
            created_by=d.get("created_by", ""),
            created_at=d.get("created_at", ""),
        )
