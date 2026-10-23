"""Core de Satellite 2.0: modelos de contrato puros (stdlib)."""

from satellite.core.errors import (
    ContractError,
    ContextError,
    ProviderError,
    SatelliteError,
    SpecValidationError,
    ToolError,
)
from satellite.core.result import (
    AgentResult,
    AgentStatus,
    ExecutionMetrics,
    Finding,
    FindingSeverity,
    ToolRecord,
)
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.core.tool_models import ToolCall, ToolResult, ToolSchema
from satellite.core.context_models import (
    ContextBundle,
    ContextLimits,
    ContextRef,
    ContextRefKind,
    ContextSection,
    ContextSnapshot,
)
from satellite.core.messages import LLMRequest, LLMResponse, Message, Role, Usage
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.proposal import (
    AgentExtension,
    AgentProposal,
    ApprovalDecision,
    ApprovalStatus,
    CanCallEntry,
    ProposedAgent,
    ProposedSkill,
    SkillProposal,
)

__all__ = [
    "SatelliteError",
    "ContractError",
    "SpecValidationError",
    "ToolError",
    "ProviderError",
    "ContextError",
    "AgentSpec",
    "AgentStatus",
    "FindingSeverity",
    "Finding",
    "ToolRecord",
    "ExecutionMetrics",
    "AgentResult",
    "ToolSchema",
    "ToolCall",
    "ToolResult",
    "ContextRefKind",
    "ContextRef",
    "ContextSection",
    "ContextBundle",
    "ContextLimits",
    "ContextSnapshot",
    "Role",
    "Message",
    "Usage",
    "LLMRequest",
    "LLMResponse",
    "EventType",
    "ExecutionEvent",
    "Task",
    "TaskStatus",
    "TaskResult",
    "ProposedAgent",
    "ProposedSkill",
    "CanCallEntry",
    "AgentProposal",
    "AgentExtension",
    "SkillProposal",
    "ApprovalDecision",
    "ApprovalStatus",
]
