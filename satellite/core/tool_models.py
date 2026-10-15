"""Modelos de la tool-layer: schema, llamada y resultado."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from satellite.core.errors import ContractError


@dataclass(frozen=True)
class ToolSchema:
    """Descripción de una tool para el LLM (JSON Schema en parameters)."""

    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ContractError("ToolSchema: name no puede estar vacío")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolSchema":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            parameters=copy.deepcopy(d.get("parameters", {})),
        )


@dataclass(frozen=True)
class ToolCall:
    """Solicitud de tool emitida por el LLM."""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ContractError("ToolCall: name (tool_id) no puede estar vacío")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": dict(self.arguments),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCall":
        return cls(
            id=d["id"],
            name=d["name"],
            arguments=dict(d.get("arguments", {})),
        )


@dataclass(frozen=True)
class ToolResult:
    """Lo que la ejecución de una tool devuelve."""

    call_id: str = ""
    success: bool = True
    output: Any = None
    error: str | None = None
    duration_ms: int = 0
    # Etiqueta de error para distinguir causas sin parsear el mensaje: p.ej.
    # "path_outside_project" (autorizable) o "protected_zone" (veto incondicional).
    error_code: str = ""

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolResult":
        return cls(
            call_id=d.get("call_id", ""),
            success=bool(d.get("success", True)),
            output=d.get("output"),
            error=d.get("error"),
            duration_ms=int(d.get("duration_ms", 0)),
            error_code=str(d.get("error_code", "")),
        )
