"""Runtime de agentes: ejecución de un nodo."""

from satellite.agents.base import (
    Agent,
    AgentContext,
    AgentFactory,
    AgentRegistry,
    PromptRegistry,
)
from satellite.agents.factory import DefaultAgentFactory
from satellite.agents.prompts import (
    ALLOWED_PLACEHOLDERS,
    DefaultPromptRegistry,
    find_placeholders,
    render_template,
)
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.runtime import LLMAgent, build_system_prompt
from satellite.agents.creator import AgentProposalGenerator, CreatorOutcome
from satellite.agents.validator import AgentProposalValidator, ValidationReport

__all__ = [
    "Agent",
    "AgentContext",
    "AgentFactory",
    "AgentRegistry",
    "PromptRegistry",
    "DefaultAgentFactory",
    "DefaultPromptRegistry",
    "DefaultAgentRegistry",
    "LLMAgent",
    "build_system_prompt",
    "AgentProposalGenerator",
    "CreatorOutcome",
    "AgentProposalValidator",
    "ValidationReport",
    "ALLOWED_PLACEHOLDERS",
    "find_placeholders",
    "render_template",
]
