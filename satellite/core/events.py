"""Modelo de eventos de ejecución (EventSink vive en events/base.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from satellite.core.errors import ContractError


class EventType(Enum):
    """Tipos de eventos emitidos por el runtime."""

    TASK_STARTED = "task_started"
    GRAPH_BUILT = "graph_built"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    TOOL_CALLED = "tool_called"
    VALIDATION_FAILED = "validation_failed"
    RETRY = "retry"
    NODE_STUCK = "node_stuck"
    TASK_FINISHED = "task_finished"
    # Texto que el modelo está escribiendo (streaming). Es efímero: no se persiste.
    TEXT_DELTA = "text_delta"
    # Un permiso espera decisión / ya se decidió (la UI deja de sondear).
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    # El asesor de plan no pudo proponer alternativas (antes desaparecía en silencio)
    PLAN_FAILED = "plan_failed"
    # Propuestas y aprobación (agentes y skills)
    AGENT_PROPOSED = "agent_proposed"
    AGENT_APPROVED = "agent_approved"
    AGENT_REJECTED = "agent_rejected"
    AGENT_CREATED = "agent_created"
    AGENT_EXTENDED = "agent_extended"
    SKILL_PROPOSED = "skill_proposed"
    SKILL_APPROVED = "skill_approved"
    SKILL_EXECUTED = "skill_executed"
    SKILL_FAILED = "skill_failed"
    # Buscada y considerada: todavía no dice que se haya usado ni ejecutado.
    SKILL_SEARCHED = "skill_searched"
    SKILL_QUARANTINED = "skill_quarantined"
    # Delegación entre agentes
    DELEGATION_STARTED = "delegation_started"
    DELEGATION_COMPLETED = "delegation_completed"
    # Gobernanza
    POLICY_VIOLATION = "policy_violation"
    SAFE_MODE_ENTERED = "safe_mode_entered"


@dataclass(frozen=True)
class ExecutionEvent:
    """Evento observable del ciclo de ejecución (para UI/CLI/MCP)."""

    event_type: EventType
    task_id: str
    node_id: str | None = None
    tool_name: str | None = None
    payload: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, EventType):
            raise ContractError(
                f"ExecutionEvent: event_type debe ser EventType, recibido {self.event_type!r}"
            )
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ContractError("ExecutionEvent: task_id no puede estar vacío")
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "tool_name": self.tool_name,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionEvent":
        raw_type = d["event_type"]
        event_type = (
            raw_type if isinstance(raw_type, EventType) else EventType(raw_type)
        )
        return cls(
            event_type=event_type,
            task_id=d["task_id"],
            node_id=d.get("node_id"),
            tool_name=d.get("tool_name"),
            payload=dict(d.get("payload", {})),
            timestamp=d.get("timestamp", ""),
        )
