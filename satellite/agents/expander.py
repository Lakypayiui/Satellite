"""AgentExpander: materializa agentes (creator → validador → aprobación → registro)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from satellite.agents.base import AgentRegistry
from satellite.agents.creator import AgentProposalGenerator
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.store import AgentCatalogStore
from satellite.agents.templates import get_template
from satellite.agents.validator import MAX_CAPABILITIES
from satellite.approval.base import ApprovalContext, ApprovalPort
from satellite.approval.policy import ApprovalPolicy
from satellite.core.errors import ToolError
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.proposal import AgentExtension, AgentProposal, ProposedAgent, ProposedSkill
from satellite.core.spec import AgentSpec
from satellite.events.base import EventSink
from satellite.kernel.manifest import is_reserved
from satellite.skills.models import SkillManifest


@dataclass(frozen=True)
class ExpansionOutcome:
    """Resultado del intento de expansión del catálogo."""

    kind: str  # created | extended | skill_created | skill_suggested | reuse
    #            | needs_clarification | rejected | error
    spec: AgentSpec | None = None
    reuse_candidate: str | None = None
    question: str | None = None
    reason: str = ""


def agent_spec_from_proposal(proposal: ProposedAgent) -> AgentSpec:
    """Convierte una ``ProposedAgent`` aprobada en un ``AgentSpec`` registrable."""
    return AgentSpec(
        id=proposal.id,
        name=proposal.name,
        description=proposal.description,
        capabilities=tuple(proposal.capabilities),
        prompt_ref=proposal.id,
        tools=tuple(proposal.tools),
        validators=tuple(proposal.validators),
        input_schema=proposal.input_schema,
        output_schema=proposal.output_schema,
        model_role=proposal.model_role,
        max_turns=proposal.max_turns,
        timeout_s=proposal.timeout_s,
        can_call=tuple(proposal.can_call),
    )


class AgentExpander:
    """Convierte una necesidad de capacidad en un agente registrado.

    No ejecuta el agente: sólo lo crea, lo aprueba, lo registra y lo persiste.
    """

    def __init__(
        self,
        creator: AgentProposalGenerator | None,
        registry: AgentRegistry,
        prompts: DefaultPromptRegistry,
        *,
        catalog_store: AgentCatalogStore | None = None,
        policy: ApprovalPolicy | None = None,
        sink: EventSink | None = None,
        skills=None,
        validator: object | None = None,
    ) -> None:
        self.creator = creator
        self.registry = registry
        self.prompts = prompts
        self.catalog_store = catalog_store
        self.policy = policy or ApprovalPolicy()
        self.sink = sink
        self.skills = skills
        # Validador determinista para plantillas (o el del creador LLM).
        self.validator = (
            validator if validator is not None else getattr(creator, "validator", None)
        )

    async def expand(
        self,
        need: str,
        *,
        task_id: str = "",
        approval_port: ApprovalPort | None = None,
        extra_context: str = "",
    ) -> ExpansionOutcome:
        """Propone, valida, aprueba y registra un agente para cubrir ``need``."""
        if self.creator is None:
            return ExpansionOutcome(
                kind="error", reason="sin creador LLM: usa una plantilla"
            )
        outcome = await self.creator.propose(need, extra_context=extra_context)
        if outcome.kind == "reuse":
            return ExpansionOutcome(kind="reuse", reuse_candidate=outcome.reuse_candidate)
        if outcome.kind == "needs_clarification":
            return ExpansionOutcome(kind="needs_clarification", question=outcome.question)
        if outcome.kind == "extend" and outcome.extension is not None:
            return await self._extend(
                outcome.extension, task_id=task_id, approval_port=approval_port
            )
        if outcome.kind == "skill_suggestion" and outcome.skill is not None:
            return await self._suggest_skill(
                outcome.skill, task_id=task_id, approval_port=approval_port
            )
        if outcome.kind != "proposed_agent" or outcome.proposal is None:
            return ExpansionOutcome(kind="error", reason=outcome.error or "sin propuesta")

        proposal = outcome.proposal
        return await self._materialize(
            proposal, task_id=task_id, approval_port=approval_port
        )

    async def _materialize(
        self,
        proposal: ProposedAgent,
        *,
        task_id: str,
        approval_port: ApprovalPort | None,
    ) -> ExpansionOutcome:
        """Aprueba, registra y persiste una propuesta ya validada."""
        record = AgentProposal(
            proposal_id=uuid.uuid4().hex[:12],
            task_id=task_id or "system",
            agent=proposal,
        )
        ctx = ApprovalContext(
            kind="agent",
            task_id=task_id,
            proposal_id=record.proposal_id,
            summary=f"crear agente '{proposal.id}': {proposal.description}",
            details={
                "capabilities": ", ".join(proposal.capabilities),
                "tools": ", ".join(proposal.tools) or "(ninguna)",
                "rationale": proposal.rationale,
            },
        )
        self._emit(
            EventType.AGENT_PROPOSED,
            task_id,
            {"proposal_id": record.proposal_id, "agent_id": proposal.id},
        )

        quota_ok = self.policy.counter.within_quotas(task_id) if task_id else True
        decision = await self.policy.decide(
            record,
            ctx,
            approval_port,
            quota_ok=quota_ok,
            validation_ok=True,
        )
        self.policy.counter.register(task_id)

        if not decision.approved:
            self._emit(
                EventType.AGENT_REJECTED,
                task_id,
                {"proposal_id": record.proposal_id, "reason": decision.reason or decision.feedback},
            )
            return ExpansionOutcome(
                kind="rejected", reason=decision.reason or decision.feedback
            )

        spec = agent_spec_from_proposal(proposal)
        self.registry.register_spec(spec)
        self.prompts.register_pair(
            spec.id, proposal.system_prompt, proposal.user_template
        )
        if self.catalog_store is not None:
            self.catalog_store.save(
                spec, system=proposal.system_prompt, user=proposal.user_template
            )

        self._emit(
            EventType.AGENT_APPROVED,
            task_id,
            {"proposal_id": record.proposal_id, "agent_id": spec.id},
        )
        self._emit(EventType.AGENT_CREATED, task_id, {"agent_id": spec.id})
        return ExpansionOutcome(kind="created", spec=spec)

    async def create_template(
        self,
        template_id: str,
        *,
        task_id: str = "",
        approval_port: ApprovalPort | None = None,
    ) -> ExpansionOutcome:
        """Crea un agente desde una plantilla estática (sin LLM), con aprobación.

        La plantilla pasa por el mismo validador determinista y por el mismo
        camino de aprobación/registro/persistencia que una propuesta del creador.
        """
        proposal = get_template(template_id)
        if proposal is None:
            return ExpansionOutcome(
                kind="error", reason=f"plantilla desconocida: {template_id!r}"
            )
        validator = self.validator
        if validator is not None:
            report = validator.validate(proposal)
            if not report.ok:
                return ExpansionOutcome(kind="error", reason="; ".join(report.errors))
        return await self._materialize(
            proposal, task_id=task_id, approval_port=approval_port
        )

    async def _extend(self, extension: AgentExtension, *, task_id: str, approval_port) -> ExpansionOutcome:
        """Añade capacidades a un agente existente (en vez de crear otro)."""
        if is_reserved(extension.agent_id):
            return ExpansionOutcome(
                kind="error",
                reason=f"'{extension.agent_id}' es del kernel y no se puede extender",
            )
        try:
            spec = self.registry.get(extension.agent_id)
        except KeyError:
            return ExpansionOutcome(
                kind="error", reason=f"agente inexistente: '{extension.agent_id}'"
            )
        merged = tuple(
            dict.fromkeys(tuple(spec.capabilities) + tuple(extension.add_capabilities))
        )
        if len(merged) > MAX_CAPABILITIES:
            return ExpansionOutcome(
                kind="rejected",
                reason=f"extender '{spec.id}' superaría {MAX_CAPABILITIES} capacidades",
            )

        proposal_id = uuid.uuid4().hex[:12]
        ctx = ApprovalContext(
            kind="agent",
            task_id=task_id,
            proposal_id=proposal_id,
            summary=f"extender agente '{spec.id}' con: {', '.join(extension.add_capabilities)}",
            details={"capabilities": ", ".join(merged), "rationale": extension.rationale},
        )
        self._emit(
            EventType.AGENT_PROPOSED,
            task_id,
            {"proposal_id": proposal_id, "agent_id": spec.id, "extend": list(extension.add_capabilities)},
        )
        quota_ok = self.policy.counter.within_quotas(task_id) if task_id else True
        decision = await self.policy.decide(
            extension.to_dict(), ctx, approval_port, quota_ok=quota_ok, validation_ok=True
        )
        self.policy.counter.register(task_id)
        if not decision.approved:
            self._emit(
                EventType.AGENT_REJECTED,
                task_id,
                {"proposal_id": proposal_id, "reason": decision.reason or decision.feedback},
            )
            return ExpansionOutcome(
                kind="rejected", reason=decision.reason or decision.feedback
            )

        updated = AgentSpec.from_dict({**spec.to_dict(), "capabilities": list(merged)})
        replace = getattr(self.registry, "replace_spec", None)
        if not callable(replace):
            return ExpansionOutcome(
                kind="error", reason="el registro no permite extender agentes"
            )
        replace(updated)
        if self.catalog_store is not None:
            self.catalog_store.save(updated)
        self._emit(
            EventType.AGENT_APPROVED,
            task_id,
            {"proposal_id": proposal_id, "agent_id": spec.id},
        )
        self._emit(
            EventType.AGENT_EXTENDED,
            task_id,
            {"agent_id": spec.id, "capabilities": list(merged)},
        )
        return ExpansionOutcome(kind="extended", spec=updated)

    async def _suggest_skill(self, skill: ProposedSkill, *, task_id: str, approval_port) -> ExpansionOutcome:
        """Una necesidad determinista se resuelve con una skill, no con un agente."""
        label = f"skill '{skill.id}': {skill.description}"
        self._emit(
            EventType.SKILL_PROPOSED,
            task_id,
            {"skill_id": skill.id, "description": skill.description},
        )
        if skill.is_clarification:
            return ExpansionOutcome(
                kind="needs_clarification", question=skill.needs_clarification
            )
        if self.skills is None or not skill.script.strip():
            return ExpansionOutcome(kind="skill_suggested", reason=label)

        proposal_id = uuid.uuid4().hex[:12]
        ctx = ApprovalContext(
            kind="skill",
            task_id=task_id,
            proposal_id=proposal_id,
            summary=f"crear {label}",
            details={"language": skill.language, "rationale": skill.rationale},
        )
        quota_ok = self.policy.counter.within_quotas(task_id) if task_id else True
        decision = await self.policy.decide(
            skill.to_dict(), ctx, approval_port, quota_ok=quota_ok, validation_ok=True
        )
        self.policy.counter.register(task_id)
        if not decision.approved:
            return ExpansionOutcome(
                kind="rejected", reason=decision.reason or decision.feedback
            )

        manifest = SkillManifest(
            id=skill.id,
            description=skill.description,
            tags=tuple(skill.tags),
            language=skill.language,
            entrypoint=skill.entrypoint,
            inputs=dict(skill.inputs),
            outputs=dict(skill.outputs),
            permissions=dict(skill.permissions),
            examples=tuple(skill.examples),
            deterministic=bool(getattr(skill, "deterministic", False)),
            created_by="agent_creator",
        )
        try:
            self.skills.register(manifest, skill.script)
        except ToolError as exc:
            return ExpansionOutcome(kind="error", reason=str(exc))
        self._emit(EventType.SKILL_APPROVED, task_id, {"skill_id": skill.id})
        return ExpansionOutcome(kind="skill_created", reason=label)

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self.sink is None:
            return
        self.sink.emit(
            ExecutionEvent(
                event_type=event_type,
                task_id=task_id or "system",
                payload=payload,
            )
        )
