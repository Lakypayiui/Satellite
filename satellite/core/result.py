"""Modelos de resultado: lo que entrega un nodo y su trazabilidad."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from satellite.core.errors import ContractError


class AgentStatus(Enum):
    """Estado final del trabajo de un nodo."""

    OK = "ok"
    FAILED = "failed"
    NEEDS_USER_INPUT = "needs_user_input"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class FindingSeverity(Enum):
    """Severidad de un hallazgo estructurado."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    """Feedback estructurado entre agentes (nunca historial completo)."""

    severity: FindingSeverity
    message: str
    location: str | None = None
    suggestion: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip():
            raise ContractError("Finding: message no puede estar vacío")
        if not isinstance(self.severity, FindingSeverity):
            raise ContractError(
                f"Finding: severity debe ser FindingSeverity, recibido {self.severity!r}"
            )

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "message": self.message,
            "location": self.location,
            "suggestion": self.suggestion,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        severity = d["severity"]
        if not isinstance(severity, FindingSeverity):
            severity = FindingSeverity(severity)
        return cls(
            severity=severity,
            message=d["message"],
            location=d.get("location"),
            suggestion=d.get("suggestion"),
        )


@dataclass(frozen=True)
class ToolRecord:
    """Trazabilidad: qué tool usó un nodo y cómo terminó."""

    tool_name: str
    arguments: dict = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ContractError("ToolRecord: tool_name no puede estar vacío")

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "success": self.success,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolRecord":
        return cls(
            tool_name=d["tool_name"],
            arguments=dict(d.get("arguments", {})),
            success=bool(d.get("success", True)),
            error=d.get("error"),
            duration_ms=int(d.get("duration_ms", 0)),
        )


@dataclass(frozen=True)
class ExecutionMetrics:
    """Métricas de una ejecución de nodo."""

    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    retries: int = 0
    cost_usd: float | None = None

    def to_dict(self) -> dict:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "duration_ms": self.duration_ms,
            "retries": self.retries,
            "cost_usd": self.cost_usd,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionMetrics":
        return cls(
            tokens_in=int(d.get("tokens_in", 0)),
            tokens_out=int(d.get("tokens_out", 0)),
            duration_ms=int(d.get("duration_ms", 0)),
            retries=int(d.get("retries", 0)),
            cost_usd=d.get("cost_usd"),
        )


@dataclass(frozen=True)
class AgentResult:
    """Lo que un nodo entrega al siguiente (resultado de ejecución)."""

    node_id: str
    spec_id: str
    status: AgentStatus
    summary: str = ""
    output: dict = field(default_factory=dict)
    tool_calls: tuple[ToolRecord, ...] = ()
    findings: tuple[Finding, ...] = ()
    context_snapshot: Any = None  # ContextSnapshot | None (ver context_models)
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ContractError("AgentResult: node_id no puede estar vacío")
        if not isinstance(self.spec_id, str) or not self.spec_id.strip():
            raise ContractError("AgentResult: spec_id no puede estar vacío")
        if not isinstance(self.status, AgentStatus):
            raise ContractError(
                f"AgentResult: status debe ser AgentStatus, recibido {self.status!r}"
            )

    def to_dict(self) -> dict:
        snapshot = self.context_snapshot
        return {
            "node_id": self.node_id,
            "spec_id": self.spec_id,
            "status": self.status.value,
            "summary": self.summary,
            "output": dict(self.output),
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "findings": [f.to_dict() for f in self.findings],
            "context_snapshot": snapshot.to_dict() if snapshot is not None else None,
            "metrics": self.metrics.to_dict(),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentResult":
        raw_status = d["status"]
        status = (
            raw_status if isinstance(raw_status, AgentStatus) else AgentStatus(raw_status)
        )
        snapshot = d.get("context_snapshot")
        from satellite.core.context_models import ContextSnapshot

        return cls(
            node_id=d["node_id"],
            spec_id=d["spec_id"],
            status=status,
            summary=d.get("summary", ""),
            output=dict(d.get("output", {})),
            tool_calls=tuple(ToolRecord.from_dict(t) for t in d.get("tool_calls", [])),
            findings=tuple(Finding.from_dict(f) for f in d.get("findings", [])),
            context_snapshot=ContextSnapshot.from_dict(snapshot) if snapshot else None,
            metrics=ExecutionMetrics.from_dict(d.get("metrics", {})),
            error=d.get("error"),
        )
