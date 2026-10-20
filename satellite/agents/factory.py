"""AgentFactory por defecto: ensambla LLMAgent con tools filtradas."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from satellite.agents.base import Agent, AgentFactory, AgentRegistry
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.runtime import LLMAgent
from satellite.core.errors import ToolError
from satellite.core.spec import AgentSpec
from satellite.kernel.manifest import RESERVED_AGENT_IDS
from satellite.providers.base import LLMProvider
from satellite.security.base import SecurityPolicy
from satellite.tools.base import Tool, ToolRegistry
from satellite.tools.context_tool import RequestContextTool

# Roles configured in ``config.json`` are "orchestrator", "context", "agents" and
# "creator". Agent specs use broader labels; map them so a per-role provider is
# actually used at execution time instead of silently falling back to the global one.
_ROLE_ALIASES: dict[str, str] = {
    "orchestrator": "orchestrator",
    "planner": "orchestrator",
    "plan": "orchestrator",
    "context": "context",
    "preprocessor": "context",
    "creator": "creator",
    "curator": "agents",
    "agent": "agents",
    "agents": "agents",
    "generated": "agents",
}


def role_key(model_role: str | None) -> str:
    """Configured role that corresponds to an agent's ``model_role``."""
    return _ROLE_ALIASES.get(str(model_role or "").strip().lower(), "agents")


class DefaultAgentFactory(AgentFactory):
    """Crea LLMAgent inyectando spec, prompt, tools y provider."""

    def __init__(
        self,
        registry: AgentRegistry,
        prompts: DefaultPromptRegistry,
        tools: ToolRegistry,
        provider: LLMProvider,
        policy: SecurityPolicy | None = None,
        context_engine=None,
        project_root: Path | None = None,
        workdir: Path | None = None,
        sandbox=None,
        delegation_manager=None,
        skills=None,
        provider_resolver: Callable[[str], LLMProvider | None] | None = None,
        subagent_provider: LLMProvider | None = None,
    ) -> None:
        self.registry = registry
        self.prompts = prompts
        self.tools = tools
        self.provider = provider
        # Optional per-role provider lookup. ``None`` keeps the single-provider
        # behavior used by tests and embedders.
        self.provider_resolver = provider_resolver
        self.policy = policy
        self.context_engine = context_engine
        self.project_root = project_root
        # Workdir de ejecución (worktree activo) que hereda cada agente; ``None`` = el
        # proyecto. Se resuelve por tarea al armar el runtime (ver ``build_runtime``).
        self.workdir = workdir
        # Sandbox del nivel configurado que hereda cada agente (procesos y filesystem).
        self.sandbox = sandbox
        self.delegation_manager = delegation_manager
        # Shared skill registry, so `search_skills`/skill execution see the catalog.
        self.skills = skills
        # Optional provider for children. ``None`` means each child inherits its
        # parent's provider; a configured ``llm.subagents`` overrides all children.
        self.subagent_provider = subagent_provider
        # request_context siempre disponible; la registra si no existe
        try:
            self.tools.get(RequestContextTool().id)
        except Exception:
            self.tools.register(RequestContextTool())

    def _resolve(self, spec_id: str) -> tuple[AgentSpec, str, str, list[Tool]]:
        """Resuelve spec + prompts + tools del agente (lo que ``create`` puede fallar)."""
        spec = self.registry.get(spec_id)
        system_prompt = self.prompts.get_system(spec.prompt_ref)
        user_template = self.prompts.get_user(spec.prompt_ref)
        tool_ids = list(spec.tools) + [RequestContextTool().id]
        if spec.can_call:
            try:
                self.tools.get("delegate")
                tool_ids.append("delegate")
            except ToolError:
                pass
        # Only specialized agents get spawn_agent. Kernel meta-agents (orchestrator,
        # creator, curator, diagram agent) never do, and children are built directly
        # without this tool.
        if spec.id not in RESERVED_AGENT_IDS:
            try:
                self.tools.get("spawn_agent")
                tool_ids.append("spawn_agent")
            except ToolError:
                pass
        # Deduplicate while preserving order: `request_context` is added always and
        # many specs already declare it. A repeated function name makes OpenAI-
        # compatible providers reject the whole request with HTTP 400.
        tool_ids = list(dict.fromkeys(tool_ids))
        if self.policy is not None:
            allowed = {
                tid
                for tid in tool_ids
                if self.policy.check_tool(tid, {})
            }
            tool_ids = [tid for tid in tool_ids if tid in allowed]
        return spec, system_prompt, user_template, self.tools.list_for(tool_ids)

    def _provider_for(self, role: str | None) -> LLMProvider:
        """Provider configured for that role, falling back to the global one."""
        if self.provider_resolver is None:
            return self.provider
        try:
            resolved = self.provider_resolver(role_key(role))
        except Exception:  # noqa: BLE001 — a resolver failure must not break creation
            return self.provider
        return resolved or self.provider

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        """Crea un LLMAgent para el spec; tools = spec.tools + request_context."""
        spec, system_prompt, user_template, tools_list = self._resolve(spec_id)
        return LLMAgent(
            spec=spec,
            system_prompt=system_prompt,
            user_template=user_template,
            tools=tools_list,
            provider=self._provider_for(model_role or spec.model_role),
            tool_registry=self.tools,
            policy=self.policy,
            context_engine=self.context_engine,
            project_root=self.project_root,
            workdir=self.workdir,
            sandbox=self.sandbox,
            delegation_manager=self.delegation_manager,
            skills=self.skills,
            spawn_child_provider=self.subagent_provider,
        )

    def can_create(self, spec_id: str) -> bool:
        """Disponibilidad real sin instanciar el agente (preflight del orquestador).

        Repite las comprobaciones de ``create`` que pueden fallar (spec, prompts y tools)
        sin construir el ``LLMAgent``: el preflight no debe instanciar ni tener efectos.
        """
        try:
            self._resolve(spec_id)
            return True
        except Exception:  # noqa: BLE001 — spec/prompt/tool ausente = no disponible
            return False
