"""Modelos de planificación: plan, gap, aclaración y alternativas (modo plan)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from satellite.core.errors import ContractError, SpecValidationError

_OPTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class PlanStep:
    """Paso mínimo del plan: una instrucción autocontenida para un agente."""

    id: str
    agent_id: str
    instruction: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("id", self.id), ("agent_id", self.agent_id)):
            if not isinstance(value, str) or not value.strip():
                raise SpecValidationError(f"PlanStep: {name} no puede estar vacío")
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise SpecValidationError(
                f"PlanStep '{self.id}': instruction no puede estar vacía"
            )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "instruction": self.instruction,
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlanStep":
        return cls(
            id=d["id"],
            agent_id=d["agent_id"],
            instruction=d["instruction"],
            depends_on=tuple(str(dep) for dep in (d.get("depends_on") or [])),
        )


@dataclass(frozen=True)
class ExecutionPlan:
    """Plan producido por el orquestador: pasos + rationale."""

    steps: tuple[PlanStep, ...]
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            raise ContractError("ExecutionPlan: steps no puede estar vacío")
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ContractError("ExecutionPlan: ids de pasos duplicados")

    def to_dict(self) -> dict:
        return {
            "type": "plan",
            "steps": [step.to_dict() for step in self.steps],
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionPlan":
        return cls(
            steps=tuple(PlanStep.from_dict(s) for s in d.get("steps", [])),
            rationale=d.get("rationale", ""),
        )


@dataclass(frozen=True)
class CapabilityGap:
    """Falta una capacidad: no hay agente apto en el catálogo."""

    missing: str
    steps_blocked: tuple[str, ...] = ()
    partial_plan: tuple[PlanStep, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.missing, str) or not self.missing.strip():
            raise SpecValidationError("CapabilityGap: missing no puede estar vacío")

    def to_dict(self) -> dict:
        return {
            "type": "capability_gap",
            "missing": self.missing,
            "steps_blocked": list(self.steps_blocked),
            "partial_plan": [step.to_dict() for step in self.partial_plan],
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityGap":
        return cls(
            missing=d["missing"],
            steps_blocked=tuple(str(s) for s in (d.get("steps_blocked") or [])),
            partial_plan=tuple(
                PlanStep.from_dict(s) for s in (d.get("partial_plan") or [])
            ),
            rationale=d.get("rationale", ""),
        )


@dataclass(frozen=True)
class ClarificationRequest:
    """El orquestador necesita que el usuario aclare algo."""

    question: str

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise SpecValidationError("ClarificationRequest: question no puede estar vacía")

    def to_dict(self) -> dict:
        return {"type": "needs_clarification", "question": self.question}

    @classmethod
    def from_dict(cls, d: dict) -> "ClarificationRequest":
        return cls(question=d["question"])


@dataclass(frozen=True)
class PlanOption:
    """Una alternativa concreta (desarrollada por el LLM) para una tarea ambigua."""

    id: str
    title: str
    summary: str
    approach: tuple[str, ...] = ()
    tradeoffs: str = ""
    effort: str = ""
    risk: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _OPTION_ID_RE.match(self.id):
            raise SpecValidationError(
                f"PlanOption: id debe ser snake/kebab-case, recibido {self.id!r}"
            )
        for name, value in (("title", self.title), ("summary", self.summary)):
            if not isinstance(value, str) or not value.strip():
                raise SpecValidationError(f"PlanOption '{self.id}': {name} no puede estar vacío")
        for step in self.approach:
            if not isinstance(step, str) or not step.strip():
                raise SpecValidationError(
                    f"PlanOption '{self.id}': approach contiene un paso vacío"
                )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "approach": list(self.approach),
            "tradeoffs": self.tradeoffs,
            "effort": self.effort,
            "risk": self.risk,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlanOption":
        return cls(
            id=str(d.get("id", "")),
            title=str(d.get("title", "")),
            summary=str(d.get("summary", "")),
            approach=tuple(str(s) for s in (d.get("approach") or []) if str(s).strip()),
            tradeoffs=str(d.get("tradeoffs", "")),
            effort=str(d.get("effort", "")),
            risk=str(d.get("risk", "")),
        )


@dataclass(frozen=True)
class PlanOptions:
    """Conjunto de alternativas propuestas para una tarea ambigua."""

    question: str
    options: tuple[PlanOption, ...]
    recommendation: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise SpecValidationError("PlanOptions: question no puede estar vacía")
        if not self.options:
            raise SpecValidationError("PlanOptions: debe haber al menos una alternativa")
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise SpecValidationError("PlanOptions: ids de alternativas duplicados")

    def option(self, option_id: str) -> PlanOption | None:
        """Alternativa por id (None si no existe)."""
        return next((option for option in self.options if option.id == option_id), None)

    @property
    def recommended(self) -> PlanOption | None:
        """Alternativa recomendada (si el modelo marcó una válida)."""
        return self.option(self.recommendation) if self.recommendation else None

    def to_dict(self) -> dict:
        return {
            "type": "plan_options",
            "question": self.question,
            "options": [option.to_dict() for option in self.options],
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlanOptions":
        options = tuple(
            PlanOption.from_dict(item) for item in (d.get("options") or []) if isinstance(item, dict)
        )
        return cls(
            question=str(d.get("question", "")),
            options=options,
            recommendation=str(d.get("recommendation", "")),
        )
