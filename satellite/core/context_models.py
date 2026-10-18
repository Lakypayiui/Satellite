"""Modelos de contexto just-in-time: qué se resuelve y cuánto pesa."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from satellite.core.errors import ContractError


class ContextRefKind(Enum):
    """Tipo de referencia de contexto."""

    PATH = "path"
    SYMBOL = "symbol"
    DIR = "dir"
    PATTERN = "pattern"
    SUMMARY = "summary"
    CONVERSATION = "conversation"
    PROJECT_RULES = "project_rules"


@dataclass(frozen=True)
class ContextRef:
    """Referencia atómica a un fragmento de contexto buscable."""

    kind: ContextRefKind
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ContextRefKind):
            raise ContractError(
                f"ContextRef: kind debe ser ContextRefKind, recibido {self.kind!r}"
            )
        if not isinstance(self.value, str) or not self.value.strip():
            raise ContractError("ContextRef: value no puede estar vacío")

    def to_dict(self) -> dict:
        return {"kind": self.kind.value, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> "ContextRef":
        raw_kind = d["kind"]
        kind = (
            raw_kind if isinstance(raw_kind, ContextRefKind) else ContextRefKind(raw_kind)
        )
        return cls(kind=kind, value=d["value"])


@dataclass(frozen=True)
class ContextSection:
    """Fragmento de contexto ya resuelto."""

    ref: ContextRef
    content: str
    tokens: int | None = None

    @property
    def chars(self) -> int:
        return len(self.content)

    def to_dict(self) -> dict:
        return {
            "ref": self.ref.to_dict(),
            "content": self.content,
            "tokens": self.tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContextSection":
        return cls(
            ref=ContextRef.from_dict(d["ref"]),
            content=d["content"],
            tokens=d.get("tokens"),
        )


@dataclass(frozen=True)
class ContextBundle:
    """Contexto MÍNIMO de un nodo: solo lo que se pidió."""

    sections: tuple[ContextSection, ...] = ()

    @property
    def total_chars(self) -> int:
        return sum(s.chars for s in self.sections)

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens or 0 for s in self.sections)

    def to_dict(self) -> dict:
        return {"sections": [s.to_dict() for s in self.sections]}

    @classmethod
    def from_dict(cls, d: dict) -> "ContextBundle":
        return cls(sections=tuple(ContextSection.from_dict(s) for s in d.get("sections", [])))


@dataclass(frozen=True)
class ApprovedExternalContext:
    """Contenido externo leído TRAS una aprobación explícita en el turno actual.

    Es el único envoltorio que el orquestador acepta para adjuntar contexto de fuera
    del proyecto. Un texto suelto puesto en ``metadata['context_external']`` (p. ej. vía
    ``extra_metadata`` o cualquier otra vía interna) se **ignora**: así la aprobación del
    usuario es la única fuente del contexto externo que se usa en el turno.
    """

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ContractError(
                f"ApprovedExternalContext: text debe ser str, recibido {type(self.text).__name__}"
            )


@dataclass(frozen=True)
class ContextLimits:
    """Límites de tamaño/volumen del contexto de un nodo."""

    max_total_chars: int | None = None
    max_sections: int | None = None
    max_chars_per_section: int | None = None

    def violations(self, bundle: ContextBundle) -> list[str]:
        """Lista de descripciones de límites excedidos (vacía si todo ok)."""
        problems: list[str] = []
        if self.max_total_chars is not None and bundle.total_chars > self.max_total_chars:
            problems.append(
                f"max_total_chars={self.max_total_chars} excedido: {bundle.total_chars}"
            )
        if self.max_sections is not None and len(bundle.sections) > self.max_sections:
            problems.append(
                f"max_sections={self.max_sections} excedido: {len(bundle.sections)}"
            )
        if self.max_chars_per_section is not None:
            for section in bundle.sections:
                if section.chars > self.max_chars_per_section:
                    problems.append(
                        f"max_chars_per_section={self.max_chars_per_section} "
                        f"excedido: {section.chars} (ref {section.ref.value})"
                    )
        return problems

    def to_dict(self) -> dict:
        return {
            "max_total_chars": self.max_total_chars,
            "max_sections": self.max_sections,
            "max_chars_per_section": self.max_chars_per_section,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContextLimits":
        return cls(
            max_total_chars=d.get("max_total_chars"),
            max_sections=d.get("max_sections"),
            max_chars_per_section=d.get("max_chars_per_section"),
        )


@dataclass(frozen=True)
class ContextSnapshot:
    """Auditoría: qué contexto recibió un nodo."""

    node_id: str
    refs: tuple[ContextRef, ...] = ()
    total_chars: int = 0
    total_tokens: int | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ContractError("ContextSnapshot: node_id no puede estar vacío")
        if not self.created_at:
            object.__setattr__(
                self, "created_at", datetime.now(timezone.utc).isoformat()
            )

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "refs": [r.to_dict() for r in self.refs],
            "total_chars": self.total_chars,
            "total_tokens": self.total_tokens,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContextSnapshot":
        return cls(
            node_id=d["node_id"],
            refs=tuple(ContextRef.from_dict(r) for r in d.get("refs", [])),
            total_chars=int(d.get("total_chars", 0)),
            total_tokens=d.get("total_tokens"),
            created_at=d.get("created_at", ""),
        )
