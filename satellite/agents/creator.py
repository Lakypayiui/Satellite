"""AgentProposalGenerator: ejecuta el agente `agent_creator` y valida su salida."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from satellite.agents.base import AgentRegistry
from satellite.agents.prompting import build_meta_messages
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.schemas import PROPOSED_AGENT_SCHEMA
from satellite.agents.validator import AgentProposalValidator
from satellite.core.errors import ContractError, SpecValidationError
from satellite.core.messages import CallContext, LLMRequest
from satellite.core.proposal import AgentExtension, ProposedAgent, ProposedSkill
from satellite.providers.base import LLMProvider
from satellite.providers.parsing import extract_json_object
from satellite.providers.prefix import fingerprint
from satellite.retrieval.sanitize import sanitize
from satellite.tools.base import ToolRegistry

PACKAGE_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class CreatorOutcome:
    """Resultado de pedir una propuesta al `agent_creator`."""

    kind: str  # proposed_agent | extend | skill_suggestion | reuse | needs_clarification | error
    proposal: ProposedAgent | None = None
    extension: AgentExtension | None = None
    skill: ProposedSkill | None = None
    reuse_candidate: str | None = None
    question: str | None = None
    error: str | None = None


class AgentProposalGenerator:
    """Convierte una necesidad de capacidad en una ``ProposedAgent`` validada."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: AgentRegistry,
        prompts: DefaultPromptRegistry,
        tools: ToolRegistry,
        *,
        validator: AgentProposalValidator | None = None,
        prompt_id: str = "agent_creator",
        max_attempts: int = 2,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.prompts = prompts
        self.tools = tools
        self.validator = validator
        self.prompt_id = prompt_id
        self.max_attempts = max_attempts

    def _tools_text(self) -> str:
        all_tools = getattr(self.tools, "all", None)
        tools = all_tools() if callable(all_tools) else []
        lines = [
            json.dumps(
                {
                    "name": tool.id,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
                ensure_ascii=False,
            )
            for tool in tools
        ]
        return "\n".join(lines) or "(sin tools)"

    def _context_text(self) -> str:
        lines = [
            f"- id={spec.id} | description={sanitize(spec.description, max_chars=400)} "
            f"| capabilities={', '.join(sanitize(c, max_chars=80) for c in spec.capabilities)}"
            for spec in self.registry.all()
        ]
        return (
            "Existing agent catalog:\n"
            + ("\n".join(lines) or "(empty)")
            + "\n\nNaming rules: id snake_case ^[a-z][a-z0-9_]{2,40}$; "
            "description = verb + object + when to use it."
            + "\nmodel_role allowed: generated."
        )

    async def propose(self, need: str, *, extra_context: str = "") -> CreatorOutcome:
        """Pide una propuesta; reintenta con los errores de validación."""
        system_prompt = self.prompts.get_system(self.prompt_id)
        user_template = self.prompts.get_user(self.prompt_id)
        context = self._context_text()
        tools_text = self._tools_text()

        feedback = ""
        last_errors: list[str] = []
        for _ in range(self.max_attempts):
            messages, stable = build_meta_messages(
                system_prompt=system_prompt,
                user_template=user_template,
                output_schema=PROPOSED_AGENT_SCHEMA,
                tools_text=tools_text,
                catalog=context,
                task=need,
                context=extra_context,
                feedback=feedback,
            )
            request = LLMRequest(
                messages=messages,
                temperature=0.0,
                stable_user_count=stable,
                call=CallContext(role="creator", catalog_fp=fingerprint(context)),
            )
            response = await self.provider.complete(request)
            data = extract_json_object(response.content or "")
            if data is None:
                last_errors.append("response without valid JSON")
                feedback = "; ".join(last_errors)
                continue

            kind = data.get("type")
            if kind == "reuse":
                return CreatorOutcome(
                    kind="reuse",
                    reuse_candidate=data.get("agent_id") or data.get("reuse_candidate"),
                )
            if kind == "needs_clarification":
                return CreatorOutcome(
                    kind="needs_clarification", question=data.get("question")
                )
            if kind == "extend":
                try:
                    extension = AgentExtension.from_dict(data)
                except (SpecValidationError, ContractError, KeyError, TypeError, ValueError) as exc:
                    last_errors.append(f"invalid extension: {exc}")
                    feedback = "; ".join(last_errors)
                    continue
                return CreatorOutcome(kind="extend", extension=extension)
            if kind == "skill_suggestion":
                try:
                    skill = ProposedSkill.from_dict(data)
                except (SpecValidationError, ContractError, KeyError, TypeError, ValueError) as exc:
                    last_errors.append(f"invalid skill suggestion: {exc}")
                    feedback = "; ".join(last_errors)
                    continue
                return CreatorOutcome(kind="skill_suggestion", skill=skill)
            if kind != "proposed_agent":
                last_errors.append(f"invalid 'type': {kind!r}")
                feedback = "; ".join(last_errors)
                continue

            try:
                proposal = ProposedAgent.from_dict(data)
            except (SpecValidationError, ContractError, KeyError, TypeError, ValueError) as exc:
                last_errors.append(f"invalid proposal: {exc}")
                feedback = "; ".join(last_errors)
                continue

            if self.validator is not None:
                report = self.validator.validate(proposal)
                if not report.ok:
                    last_errors.extend(report.errors)
                    feedback = "; ".join(last_errors)
                    continue

            return CreatorOutcome(kind="proposed_agent", proposal=proposal)

        return CreatorOutcome(
            kind="error",
            error="; ".join(last_errors) or "no proposal was produced",
        )
