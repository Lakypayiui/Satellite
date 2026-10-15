"""Tarea (instrucción del usuario) y su resultado agregado."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from uuid import uuid4

from satellite.core.errors import ContractError
from satellite.core.result import ExecutionMetrics, Finding


@dataclass(frozen=True)
class Task:
    """Objetivo concreto solicitado por el usuario."""

    goal: str
    project_root: Path
    metadata: dict = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: uuid4().hex[:12])

    def __post_init__(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ContractError("Task: goal no puede estar vacío")
        if not isinstance(self.project_root, Path) or not self.project_root.is_absolute():
            raise ContractError(
                f"Task: project_root debe ser Path absoluto, recibido {self.project_root!r}"
            )
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ContractError("Task: task_id no puede estar vacío")

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "project_root": str(self.project_root),
            "metadata": dict(self.metadata),
            "task_id": self.task_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        project_root = d["project_root"]
        return cls(
            goal=d["goal"],
            project_root=Path(project_root),
            metadata=dict(d.get("metadata", {})),
            task_id=d.get("task_id", ""),
        )


class TaskStatus(Enum):
    """Estado global de una tarea."""

    OK = "ok"
    FAILED = "failed"
    NEEDS_USER_INPUT = "needs_user_input"
    PARTIAL = "partial"


@dataclass(frozen=True)
class TaskResult:
    """Resultado agregado de una tarea completa."""

    task_id: str
    status: TaskStatus
    summary: str = ""
    output: dict | None = None
    findings: tuple[Finding, ...] = ()
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ContractError("TaskResult: task_id no puede estar vacío")
        if not isinstance(self.status, TaskStatus):
            raise ContractError(
                f"TaskResult: status debe ser TaskStatus, recibido {self.status!r}"
            )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "summary": self.summary,
            "output": self.output,
            "findings": [f.to_dict() for f in self.findings],
            "metrics": self.metrics.to_dict(),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskResult":
        raw_status = d["status"]
        status = (
            raw_status if isinstance(raw_status, TaskStatus) else TaskStatus(raw_status)
        )
        return cls(
            task_id=d["task_id"],
            status=status,
            summary=d.get("summary", ""),
            output=d.get("output"),
            findings=tuple(Finding.from_dict(f) for f in d.get("findings", [])),
            metrics=ExecutionMetrics.from_dict(d.get("metrics", {})),
            error=d.get("error"),
            started_at=d.get("started_at", ""),
            finished_at=d.get("finished_at", ""),
        )
