"""AgentFactory por defecto: ensambla LLMAgent con tools filtradas."""

from __future__ import annotations

from pathlib import Path

from satellite.agents.base import Agent, AgentFactory, AgentRegistry
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.runtime import LLMAgent
from satellite.core.errors import ToolError
from satellite.providers.base import LLMProvider
from satellite.security.base import SecurityPolicy
from satellite.tools.base import ToolRegistry
from satellite.tools.context_tool import RequestContextTool


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
        delegation_manager=None,
    ) -> None:
        self.registry = registry
        self.prompts = prompts
        self.tools = tools
        self.provider = provider
        self.policy = policy
        self.context_engine = context_engine
        self.project_root = project_root
        self.delegation_manager = delegation_manager
        # request_context siempre disponible; la registra si no existe
        try:
            self.tools.get(RequestContextTool().id)
        except Exception:
            self.tools.register(RequestContextTool())

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        """Crea un LLMAgent para el spec; tools = spec.tools + request_context."""
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
        if self.policy is not None:
            allowed = {
                tid
                for tid in tool_ids
                if self.policy.check_tool(tid, {})
            }
            tool_ids = [tid for tid in tool_ids if tid in allowed]
        tools_list = self.tools.list_for(tool_ids)
        return LLMAgent(
            spec=spec,
            system_prompt=system_prompt,
            user_template=user_template,
            tools=tools_list,
            provider=self.provider,
            tool_registry=self.tools,
            policy=self.policy,
            context_engine=self.context_engine,
            project_root=self.project_root,
            delegation_manager=self.delegation_manager,
        )
