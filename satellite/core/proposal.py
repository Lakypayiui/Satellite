"""Propuestas de agente/skill y decisión de aprobación (contratos puros).

Estos modelos viajan del creador (LLM `agent_creator`) al puerto de aprobación
humana y, tras la aprobación, al registro. Son datos: se serializan con
`to_dict()`/`from_dict()` simétricos, como el resto de `core/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from satellite.core.errors import ContractError, SpecValidationError
from satellite.core.spec import ALLOWED_LIFECYCLES, CanCallEntry

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _require_snake_case(value: Any, field_name: str, owner: str) -> str:
    """Valida que un identificador sea snake_case no vacío."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise SpecValidationError(
            f"{owner}: {field_name} debe ser snake_case (^[a-z][a-z0-9_]*$), "
            f"recibido {value!r}"
        )
    return value


def _require_non_empty(value: Any, field_name: str, owner: str) -> str:
    """Valida que un campo de texto no esté vacío."""
    if not isinstance(value, str) or not value.strip():
        raise SpecValidationError(f"{owner}: {field_name} no puede estar vacío")
    return value


def _as_str_tuple(value: Any, field_name: str, owner: str) -> tuple[str, ...]:
    """Normaliza listas/tuplas de strings no vacíos."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise SpecValidationError(f"{owner}: {field_name} debe ser lista o tupla")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SpecValidationError(
                f"{owner}: {field_name} contiene un valor vacío: {item!r}"
            )
        result.append(item)
    return tuple(result)


def _deep_copy(value: Any) -> Any:
    """Copia profunda de dicts/listas; devuelve None si es None."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalStatus(Enum):
    """Estado de una propuesta en el flujo de aprobación."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"


def _can_call_from(value: Any) -> tuple[CanCallEntry, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise SpecValidationError("can_call debe ser lista o tupla")
    entries: list[CanCallEntry] = []
    for item in value:
        if isinstance(item, CanCallEntry):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(CanCallEntry(agent_id=item))
        elif isinstance(item, dict):
            entries.append(CanCallEntry.from_dict(item))
        else:
            raise SpecValidationError(f"can_call contiene un valor inválido: {item!r}")
    return tuple(entries)


@dataclass(frozen=True)
class ProposedAgent:
    """Agente propuesto por el `agent_creator` antes de aprobación.

    Si ``needs_clarification`` está presente, la propuesta sólo pide aclaración
    al usuario y no se valida el resto de campos (nunca se rellena a ciegas).
    """

    id: str = ""
    name: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    validators: tuple[str, ...] = ()
    model_role: str = "agent"
    max_turns: int = 1
    timeout_s: int = 120
    system_prompt: str = ""
    user_template: str = ""
    input_schema: dict | None = None
    output_schema: dict | None = None
    can_call: tuple[CanCallEntry, ...] = ()
    skills_base: tuple[str, ...] = ()
    example_tasks: tuple[str, ...] = ()
    rationale: str = ""
    reuse_checked: bool = False
    reuse_candidate: str | None = None
    needs_clarification: str | None = None
    lifecycle: str = "persistent"

    def __post_init__(self) -> None:
        owner = "ProposedAgent"
        if self.needs_clarification is not None:
            _require_non_empty(self.needs_clarification, "needs_clarification", owner)
            return
        _require_snake_case(self.id, "id", owner)
        _require_non_empty(self.name, "name", owner)
        _require_non_empty(self.model_role, "model_role", owner)
        _require_non_empty(self.system_prompt, "system_prompt", owner)
        if not isinstance(self.user_template, str):
            raise SpecValidationError(
                f"{owner} '{self.id}': user_template debe ser string"
            )
        _require_non_empty(self.rationale, "rationale", owner)
        if not self.capabilities:
            raise SpecValidationError(
                f"{owner} '{self.id}': capabilities debe tener al menos una capacidad"
            )
        for name, values in (
            ("capabilities", self.capabilities),
            ("tools", self.tools),
            ("validators", self.validators),
            ("skills_base", self.skills_base),
            ("example_tasks", self.example_tasks),
        ):
            _as_str_tuple(values, name, f"{owner} '{self.id}'")
        if not isinstance(self.max_turns, int) or self.max_turns < 0:
            raise SpecValidationError(
                f"{owner} '{self.id}': max_turns debe ser int >= 0, "
                f"recibido {self.max_turns!r}"
            )
        if not isinstance(self.timeout_s, int) or self.timeout_s <= 0:
            raise SpecValidationError(
                f"{owner} '{self.id}': timeout_s debe ser int > 0, "
                f"recibido {self.timeout_s!r}"
            )
        if not isinstance(self.reuse_checked, bool):
            raise SpecValidationError(
                f"{owner} '{self.id}': reuse_checked debe ser bool"
            )
        for entry in self.can_call:
            if not isinstance(entry, CanCallEntry):
                raise SpecValidationError(
                    f"{owner} '{self.id}': can_call debe contener CanCallEntry"
                )
        if self.lifecycle not in ALLOWED_LIFECYCLES:
            raise SpecValidationError(
                f"{owner} '{self.id}': lifecycle debe ser uno de "
                f"{', '.join(ALLOWED_LIFECYCLES)} (recibido {self.lifecycle!r})"
            )

    @property
    def is_clarification(self) -> bool:
        """True si la propuesta sólo pide aclaración al usuario."""
        return self.needs_clarification is not None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "validators": list(self.validators),
            "model_role": self.model_role,
            "max_turns": self.max_turns,
            "timeout_s": self.timeout_s,
            "system_prompt": self.system_prompt,
            "user_template": self.user_template,
            "input_schema": _deep_copy(self.input_schema),
            "output_schema": _deep_copy(self.output_schema),
            "can_call": [c.to_dict() for c in self.can_call],
            "skills_base": list(self.skills_base),
            "example_tasks": list(self.example_tasks),
            "rationale": self.rationale,
            "reuse_checked": self.reuse_checked,
            "reuse_candidate": self.reuse_candidate,
            "needs_clarification": self.needs_clarification,
            "lifecycle": self.lifecycle,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProposedAgent":
        if not isinstance(d, dict):
            raise SpecValidationError(
                f"ProposedAgent.from_dict: se esperaba dict, "
                f"recibido {type(d).__name__}"
            )
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            description=d.get("description", ""),
            capabilities=_as_str_tuple(d.get("capabilities", ()), "capabilities", "ProposedAgent"),
            tools=_as_str_tuple(d.get("tools", ()), "tools", "ProposedAgent"),
            validators=_as_str_tuple(d.get("validators", ()), "validators", "ProposedAgent"),
            model_role=d.get("model_role", "agent"),
            max_turns=int(d.get("max_turns", 1)),
            timeout_s=int(d.get("timeout_s", 120)),
            system_prompt=d.get("system_prompt", ""),
            user_template=d.get("user_template", ""),
            input_schema=_deep_copy(d.get("input_schema")),
            output_schema=_deep_copy(d.get("output_schema")),
            can_call=_can_call_from(d.get("can_call")),
            skills_base=_as_str_tuple(d.get("skills_base", ()), "skills_base", "ProposedAgent"),
            example_tasks=_as_str_tuple(d.get("example_tasks", ()), "example_tasks", "ProposedAgent"),
            rationale=d.get("rationale", ""),
            reuse_checked=bool(d.get("reuse_checked", False)),
            reuse_candidate=d.get("reuse_candidate"),
            needs_clarification=d.get("needs_clarification"),
            lifecycle=d.get("lifecycle", "persistent"),
        )


@dataclass(frozen=True)
class ProposedSkill:
    """Skill propuesta por un agente antes de aprobación (script + manifest).

    Si ``needs_clarification`` está presente, sólo pide aclaración al usuario.
    """

    id: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    language: str = "python"
    entrypoint: str = ""
    script: str = ""
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    examples: tuple[str, ...] = ()
    rationale: str = ""
    reuse_checked: bool = False
    deterministic: bool = False
    needs_clarification: str | None = None

    def __post_init__(self) -> None:
        owner = "ProposedSkill"
        if self.needs_clarification is not None:
            _require_non_empty(self.needs_clarification, "needs_clarification", owner)
            return
        _require_snake_case(self.id, "id", owner)
        _require_non_empty(self.description, "description", owner)
        _require_non_empty(self.language, "language", owner)
        _require_non_empty(self.entrypoint, "entrypoint", owner)
        if not isinstance(self.script, str):
            raise SpecValidationError(f"{owner} '{self.id}': script debe ser string")
        _require_non_empty(self.rationale, "rationale", owner)
        _as_str_tuple(self.tags, "tags", f"{owner} '{self.id}'")
        _as_str_tuple(self.examples, "examples", f"{owner} '{self.id}'")
        if not isinstance(self.reuse_checked, bool):
            raise SpecValidationError(
                f"{owner} '{self.id}': reuse_checked debe ser bool"
            )
        if not isinstance(self.deterministic, bool):
            raise SpecValidationError(
                f"{owner} '{self.id}': deterministic debe ser bool"
            )

    @property
    def is_clarification(self) -> bool:
        """True si la propuesta sólo pide aclaración al usuario."""
        return self.needs_clarification is not None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "tags": list(self.tags),
            "language": self.language,
            "entrypoint": self.entrypoint,
            "script": self.script,
            "inputs": _deep_copy(self.inputs) or {},
            "outputs": _deep_copy(self.outputs) or {},
            "permissions": _deep_copy(self.permissions) or {},
            "examples": list(self.examples),
            "rationale": self.rationale,
            "reuse_checked": self.reuse_checked,
            "deterministic": bool(self.deterministic),
            "needs_clarification": self.needs_clarification,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProposedSkill":
        if not isinstance(d, dict):
            raise SpecValidationError(
                f"ProposedSkill.from_dict: se esperaba dict, "
                f"recibido {type(d).__name__}"
            )
        return cls(
            id=d.get("id", ""),
            description=d.get("description", ""),
            tags=_as_str_tuple(d.get("tags", ()), "tags", "ProposedSkill"),
            language=d.get("language", "python"),
            entrypoint=d.get("entrypoint", ""),
            script=d.get("script", ""),
            inputs=_deep_copy(d.get("inputs")) or {},
            outputs=_deep_copy(d.get("outputs")) or {},
            permissions=_deep_copy(d.get("permissions")) or {},
            examples=_as_str_tuple(d.get("examples", ()), "examples", "ProposedSkill"),
            rationale=d.get("rationale", ""),
            reuse_checked=bool(d.get("reuse_checked", False)),
            needs_clarification=d.get("needs_clarification"),
        )


@dataclass(frozen=True)
class AgentExtension:
    """Extensión de un agente existente: añadir capacidades, no crear otro."""

    agent_id: str
    add_capabilities: tuple[str, ...]
    rationale: str = ""

    def __post_init__(self) -> None:
        _require_snake_case(self.agent_id, "agent_id", "AgentExtension")
        if not self.add_capabilities:
            raise SpecValidationError(
                "AgentExtension: add_capabilities debe tener al menos una capacidad"
            )
        for capability in self.add_capabilities:
            if not isinstance(capability, str) or not capability.strip():
                raise SpecValidationError(
                    "AgentExtension: add_capabilities contiene un valor vacío"
                )

    def to_dict(self) -> dict:
        return {
            "type": "extend",
            "agent_id": self.agent_id,
            "add_capabilities": list(self.add_capabilities),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentExtension":
        return cls(
            agent_id=str(d.get("agent_id", "")),
            add_capabilities=_as_str_tuple(
                d.get("add_capabilities", ()), "add_capabilities", "AgentExtension"
            ),
            rationale=str(d.get("rationale", "")),
        )


@dataclass(frozen=True)
class ApprovalDecision:
    """Decisión del usuario sobre una propuesta."""

    status: ApprovalStatus
    reason: str = ""
    feedback: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, ApprovalStatus):
            raise ContractError(
                f"ApprovalDecision: status debe ser ApprovalStatus, "
                f"recibido {self.status!r}"
            )

    @property
    def approved(self) -> bool:
        """True si la propuesta fue aprobada."""
        return self.status is ApprovalStatus.APPROVED

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "feedback": self.feedback,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalDecision":
        raw = d["status"]
        status = raw if isinstance(raw, ApprovalStatus) else ApprovalStatus(raw)
        return cls(status=status, reason=d.get("reason", ""), feedback=d.get("feedback", ""))


@dataclass(frozen=True)
class AgentProposal:
    """Envoltorio de una `ProposedAgent` en el flujo de aprobación."""

    proposal_id: str
    task_id: str
    agent: ProposedAgent
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = ""
    decision: ApprovalDecision | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.proposal_id, "proposal_id", "AgentProposal")
        _require_non_empty(self.task_id, "task_id", "AgentProposal")
        if not isinstance(self.agent, ProposedAgent):
            raise ContractError("AgentProposal: agent debe ser ProposedAgent")
        if not isinstance(self.status, ApprovalStatus):
            raise ContractError("AgentProposal: status debe ser ApprovalStatus")
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "task_id": self.task_id,
            "agent": self.agent.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "decision": self.decision.to_dict() if self.decision else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentProposal":
        raw = d.get("status", ApprovalStatus.PENDING)
        status = raw if isinstance(raw, ApprovalStatus) else ApprovalStatus(raw)
        decision = d.get("decision")
        return cls(
            proposal_id=d["proposal_id"],
            task_id=d["task_id"],
            agent=ProposedAgent.from_dict(d["agent"]),
            status=status,
            created_at=d.get("created_at", ""),
            decision=ApprovalDecision.from_dict(decision) if decision else None,
        )


@dataclass(frozen=True)
class SkillProposal:
    """Envoltorio de una `ProposedSkill` en el flujo de aprobación."""

    proposal_id: str
    task_id: str
    skill: ProposedSkill
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = ""
    decision: ApprovalDecision | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.proposal_id, "proposal_id", "SkillProposal")
        _require_non_empty(self.task_id, "task_id", "SkillProposal")
        if not isinstance(self.skill, ProposedSkill):
            raise ContractError("SkillProposal: skill debe ser ProposedSkill")
        if not isinstance(self.status, ApprovalStatus):
            raise ContractError("SkillProposal: status debe ser ApprovalStatus")
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "task_id": self.task_id,
            "skill": self.skill.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "decision": self.decision.to_dict() if self.decision else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillProposal":
        raw = d.get("status", ApprovalStatus.PENDING)
        status = raw if isinstance(raw, ApprovalStatus) else ApprovalStatus(raw)
        decision = d.get("decision")
        return cls(
            proposal_id=d["proposal_id"],
            task_id=d["task_id"],
            skill=ProposedSkill.from_dict(d["skill"]),
            status=status,
            created_at=d.get("created_at", ""),
            decision=ApprovalDecision.from_dict(decision) if decision else None,
        )
