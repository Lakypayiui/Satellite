"""Tests de contrato de las interfaces ABC."""

import inspect
from pathlib import Path

import pytest

from satellite.providers.base import LLMProvider
from satellite.tools.base import Tool, ToolContext, ToolRegistry
from satellite.agents.base import (
    Agent,
    AgentContext,
    AgentFactory,
    AgentRegistry,
    PromptRegistry,
)
from satellite.context.base import ContextEngine
from satellite.validation.base import CheckResult, ValidationContext, Validator
from satellite.orchestration.base import Orchestrator
from satellite.events.base import EventSink
from satellite.security.base import SecurityPolicy
from satellite.persistence.base import Store

from satellite.core.messages import LLMRequest, LLMResponse
from satellite.core.tool_models import ToolResult, ToolCall
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult
from satellite.core.context_models import ContextBundle, ContextLimits, ContextSnapshot
from satellite.core.events import ExecutionEvent, EventType
from satellite.core.errors import ToolError


# --- Implementaciones mínimas para probar instanciabilidad ---

class FakeLLM(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="ok")


class FakeTool(Tool):
    @property
    def id(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "tool de prueba"

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        return ToolResult(call_id="c1", success=True, output="ok")


class FakeToolRegistry(ToolRegistry):
    def register(self, tool: Tool) -> None:
        pass

    def get(self, tool_id: str) -> Tool:
        raise ToolError(f"no existe {tool_id}")

    def list_for(self, tool_ids: list[str]) -> list[Tool]:
        return []

    def schemas_for(self, tool_ids: list[str]) -> list:
        return []

    async def execute(self, call: ToolCall, tool_ctx: ToolContext) -> ToolResult:
        return ToolResult(call_id=call.id, success=True)


class FakeAgent(Agent):
    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(node_id="n1", spec_id="s", status=AgentStatus.OK)


class FakeAgentFactory(AgentFactory):
    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        return FakeAgent()


class FakeAgentRegistry(AgentRegistry):
    def register_spec(self, spec: AgentSpec) -> None:
        pass

    def get(self, spec_id: str) -> AgentSpec:
        return AgentSpec(id=spec_id, name="X", capabilities=("c",), prompt_ref="p")

    def find_by_capability(self, text: str) -> list[AgentSpec]:
        return []

    def all(self) -> list[AgentSpec]:
        return []


class FakePromptRegistry(PromptRegistry):
    def get(self, prompt_id: str) -> str:
        return ""

    def register(self, prompt_id: str, template: str) -> None:
        pass


class FakeContextEngine(ContextEngine):
    async def resolve(self, refs, limits: ContextLimits | None = None) -> ContextBundle:
        return ContextBundle()

    async def expand(self, node_id: str, new_refs) -> ContextBundle:
        return ContextBundle()

    async def invalidate(self, paths: list[str]) -> None:
        pass

    async def snapshot(self, node_id: str) -> ContextSnapshot:
        return ContextSnapshot(node_id=node_id)


class FakeValidator(Validator):
    async def validate(self, result: AgentResult, vctx: ValidationContext) -> CheckResult:
        return CheckResult(validator_id="v1", ok=True)


def task_status_ok():
    from satellite.core.task import TaskStatus
    return TaskStatus.OK


class FakeOrchestrator(Orchestrator):
    async def execute(self, task: Task) -> TaskResult:
        return TaskResult(task_id=task.task_id, status=task_status_ok())


class FakeEventSink(EventSink):
    def __init__(self):
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        pass


class FakeSecurityPolicy(SecurityPolicy):
    def check_tool(self, tool_id: str, arguments: dict, allowed_tools: set[str] | None = None) -> bool:
        return True


class FakeStore(Store):
    async def save_task(self, task: Task) -> None:
        pass

    async def load_task(self, task_id: str) -> Task:
        raise KeyError(task_id)

    async def append_event(self, task_id: str, event: ExecutionEvent) -> None:
        pass

    async def load_events(self, task_id: str) -> list[ExecutionEvent]:
        return []

    async def save_state(self, task_id: str, state: dict) -> None:
        pass

    async def load_state(self, task_id: str) -> dict | None:
        return None


# --- Tests ---

def test_abcs_not_instantiable() -> None:
    with pytest.raises(TypeError):
        LLMProvider()
    with pytest.raises(TypeError):
        Tool()
    with pytest.raises(TypeError):
        ToolRegistry()
    with pytest.raises(TypeError):
        Agent()
    with pytest.raises(TypeError):
        AgentFactory()
    with pytest.raises(TypeError):
        AgentRegistry()
    with pytest.raises(TypeError):
        PromptRegistry()
    with pytest.raises(TypeError):
        ContextEngine()
    with pytest.raises(TypeError):
        Validator()
    with pytest.raises(TypeError):
        Orchestrator()
    with pytest.raises(TypeError):
        EventSink()
    with pytest.raises(TypeError):
        SecurityPolicy()
    with pytest.raises(TypeError):
        Store()


def test_fake_llm_instanciable_and_coroutine() -> None:
    fake = FakeLLM()
    assert isinstance(fake, LLMProvider)
    assert inspect.iscoroutinefunction(FakeLLM.complete)


def test_fake_tool_instanciable_and_coroutine() -> None:
    tool = FakeTool()
    assert tool.id == "fake_tool"
    assert inspect.iscoroutinefunction(FakeTool.execute)


def test_minimal_subclasses_instantiate() -> None:
    FakeToolRegistry()
    FakeAgent()
    FakeAgentFactory()
    FakeAgentRegistry()
    FakePromptRegistry()
    FakeContextEngine()
    FakeValidator()
    FakeOrchestrator()
    FakeEventSink()
    FakeSecurityPolicy()
    FakeStore()


def test_minimal_security_policy_denies_paths_and_commands(tmp_path: Path) -> None:
    """Una política que sólo implementa el ABC hereda comprobaciones deny-by-default."""
    policy = FakeSecurityPolicy()
    assert policy.check_path(tmp_path / "a.txt", tmp_path) is False
    assert policy.check_command(["python", "-c", "print(1)"]) is False


def test_event_sink_emit_works() -> None:
    sink = FakeEventSink()
    event = ExecutionEvent(event_type=EventType.TASK_STARTED, task_id="t1")
    sink.emit(event)
    assert len(sink.events) == 1


def test_tool_output_arbitrary() -> None:
    tr = ToolResult(call_id="c", success=True, output={"nested": [1, 2]})
    assert tr.output["nested"] == [1, 2]
