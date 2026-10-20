"""Subagent ownership, budget and child restrictions."""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.agents.base import AgentContext
from satellite.agents.runtime import LLMAgent, SPAWN_RULES, build_system_prompt
from satellite.agents.subagents import (
    MAX_CHILDREN_PER_PARENT,
    OwnedPath,
    SpawnGuard,
    SpawnManager,
    SpawnResult,
)
from satellite.core.context_models import ContextBundle
from satellite.core.messages import LLMRequest, LLMResponse, Role
from satellite.core.spec import AgentSpec
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider
from satellite.tools.base import ToolContext
from satellite.tools.fs import WriteFileTool
from satellite.tools.registry import DefaultToolRegistry
from satellite.tools.spawn_agent import SpawnAgentTool


class DummyParent:
    def __init__(self, tmp_path: Path) -> None:
        self.spec = AgentSpec(
            id="code_writer",
            name="Code writer",
            capabilities=("write code",),
            prompt_ref="code_writer",
            max_turns=3,
            timeout_s=10,
        )
        self.project_root = tmp_path
        self.workdir = tmp_path
        self._task_state: dict = {}


def run(coro):
    return asyncio.run(coro)


def test_spawn_guard_only_allows_owned_files(tmp_path: Path) -> None:
    owned = (OwnedPath(str((tmp_path / "owned.py").resolve()), ""),)
    guard = SpawnGuard(owned=owned, readonly=(), base=tmp_path)

    assert guard.can_write("owned.py")
    assert guard.can_write_arguments({"path": str(tmp_path / "owned.py")})
    assert not guard.can_write("other.py")
    assert not guard.can_write_arguments({"out_path": "diagrams/x.svg"})


def test_region_owner_cannot_rewrite_the_whole_file(tmp_path: Path) -> None:
    guard = SpawnGuard(
        owned=(OwnedPath(str((tmp_path / "module.py").resolve()), "parse"),),
        readonly=(),
        base=tmp_path,
    )
    assert guard.allows_tool("edit_file", {"path": "module.py"})
    assert not guard.allows_tool("write_file", {"path": "module.py"})
    assert not guard.allows_tool("render_diagram", {"out_path": "module.py"})


def test_spawn_budget_and_parallel_reservation(tmp_path: Path) -> None:
    parent = DummyParent(tmp_path)
    manager = SpawnManager(parent)

    async def fake_child(**kwargs):
        await asyncio.sleep(0.01)
        return SpawnResult(
            agent_id=kwargs["agent_id"],
            status="completed",
            summary="ok",
        )

    manager._run_child = fake_child  # type: ignore[method-assign]

    async def scenario():
        return await asyncio.gather(
            *(
                manager.spawn(
                    task="work",
                    context="",
                    files_owned=[f"file{i}.py"],
                    files_readonly=[],
                )
                for i in range(MAX_CHILDREN_PER_PARENT + 1)
            )
        )

    results = run(scenario())
    assert sum(1 for item in results if item.status == "completed") == MAX_CHILDREN_PER_PARENT
    assert sum(1 for item in results if item.error == "subagent_limit_reached") == 1
    assert parent._task_state["subagents"]["count"] == MAX_CHILDREN_PER_PARENT


def test_budget_is_per_parent_but_ownership_is_shared(tmp_path: Path) -> None:
    first = DummyParent(tmp_path)
    second = DummyParent(tmp_path)
    third = DummyParent(tmp_path)
    second._task_state = first._task_state
    third._task_state = first._task_state
    first_manager = SpawnManager(first)
    second_manager = SpawnManager(second)
    third_manager = SpawnManager(third)

    async def fake_child(**kwargs):
        return SpawnResult(agent_id=kwargs["agent_id"], status="completed", summary="ok")

    first_manager._run_child = fake_child  # type: ignore[method-assign]
    second_manager._run_child = fake_child  # type: ignore[method-assign]
    third_manager._run_child = fake_child  # type: ignore[method-assign]

    async def scenario():
        one = await asyncio.gather(
            *(
                first_manager.spawn(
                    task="one",
                    context="",
                    files_owned=[f"first{i}.py"],
                    files_readonly=[],
                )
                for i in range(MAX_CHILDREN_PER_PARENT)
            )
        )
        two = await asyncio.gather(
            *(
                second_manager.spawn(
                    task="two",
                    context="",
                    files_owned=[f"second{i}.py"],
                    files_readonly=[],
                )
                for i in range(MAX_CHILDREN_PER_PARENT)
            )
        )
        conflict = await third_manager.spawn(
            task="conflict",
            context="",
            files_owned=["first0.py"],
            files_readonly=[],
        )
        return one, two, conflict

    one, two, conflict = run(scenario())
    assert all(item.status == "completed" for item in one)
    assert all(item.status == "completed" for item in two)
    assert conflict.status == "failed"
    assert conflict.error == "subagent_ownership_conflict"


def test_spawn_ownership_conflict_is_rejected(tmp_path: Path) -> None:
    parent = DummyParent(tmp_path)
    manager = SpawnManager(parent)

    async def fake_child(**kwargs):
        return SpawnResult(agent_id=kwargs["agent_id"], status="completed", summary="ok")

    manager._run_child = fake_child  # type: ignore[method-assign]

    async def scenario():
        first = await manager.spawn(
            task="one",
            context="",
            files_owned=["src/module.py::first"],
            files_readonly=[],
        )
        second = await manager.spawn(
            task="two",
            context="",
            files_owned=["src/module.py::second"],
            files_readonly=[],
        )
        conflict = await manager.spawn(
            task="whole",
            context="",
            files_owned=["src/module.py"],
            files_readonly=[],
        )
        return first, second, conflict

    first, second, conflict = run(scenario())
    assert first.status == "completed"
    assert second.status == "completed"
    assert conflict.status == "failed"
    assert conflict.error == "subagent_ownership_conflict"


def test_child_cannot_call_spawn_agent(tmp_path: Path) -> None:
    tool = SpawnAgentTool()
    ctx = ToolContext(project_root=tmp_path, is_child=True)
    result = run(tool.execute({"task": "do work"}, ctx))
    assert not result.success
    assert result.error_code == "subagent_cannot_spawn"


def test_parent_prompt_has_subagent_rules_and_child_prompt_does_not() -> None:
    parent = build_system_prompt("base", spawn_enabled=True)
    child = build_system_prompt("base", spawn_enabled=False)
    assert SPAWN_RULES in parent
    assert "# SUBAGENTS" not in child


def test_child_context_has_no_parent_history() -> None:
    ctx = AgentContext()
    assert ctx.context.sections == ()
    assert ctx.feedback == ()


def test_spawn_calls_in_one_turn_run_concurrently(tmp_path: Path) -> None:
    class ParentProvider(LLMProvider):
        def __init__(self) -> None:
            self.requests: list[LLMRequest] = []

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            tool_messages = [m for m in request.messages if m.role is Role.TOOL]
            if not tool_messages:
                return LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="spawn-1",
                            name="spawn_agent",
                            arguments={
                                "task": "write a.py",
                                "context": "a.py returns one",
                                "files_owned": ["a.py"],
                                "files_readonly": [],
                            },
                        ),
                        ToolCall(
                            id="spawn-2",
                            name="spawn_agent",
                            arguments={
                                "task": "write b.py",
                                "context": "b.py returns two",
                                "files_owned": ["b.py"],
                                "files_readonly": [],
                            },
                        ),
                    ),
                )
            return LLMResponse(content='{"summary":"integrated"}')

    class ChildProvider(LLMProvider):
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.05)
            self.active -= 1
            return LLMResponse(
                content='{"status":"completed","summary":"child ok","files_modified":[]}'
            )

    parent_provider = ParentProvider()
    child_provider = ChildProvider()
    registry = DefaultToolRegistry()
    registry.register(SpawnAgentTool())
    spec = AgentSpec(
        id="code_writer",
        name="Code writer",
        capabilities=("write code",),
        prompt_ref="code_writer",
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        max_turns=3,
        timeout_s=10,
    )
    agent = LLMAgent(
        spec=spec,
        system_prompt="base",
        user_template="{task}\n{context}",
        tools=[SpawnAgentTool()],
        provider=parent_provider,
        tool_registry=registry,
        project_root=tmp_path,
        workdir=tmp_path,
        spawn_child_provider=child_provider,
    )
    ctx = AgentContext(
        context=ContextBundle(),
        task_id="task-1",
        node_id="n1",
        project_root=tmp_path,
        task_state={},
    )
    result = run(agent.execute({"node_id": "n1", "goal": "write both"}, ctx))

    assert result.output == {"summary": "integrated"}
    assert child_provider.peak == 2
    assert agent.spawn_manager is not None
    assert ctx.task_state["subagents"]["count"] == 2
    tool_messages = [
        message
        for message in parent_provider.requests[-1].messages
        if message.role is Role.TOOL
    ]
    assert len(tool_messages) == 2
    assert all('"status": "completed"' in message.content for message in tool_messages)


def test_child_write_outside_ownership_is_blocked(tmp_path: Path) -> None:
    class ChildProvider(LLMProvider):
        def __init__(self) -> None:
            self.requests: list[LLMRequest] = []

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            if not any(m.role is Role.TOOL for m in request.messages):
                return LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="write-1",
                            name="write_file",
                            arguments={"path": "stolen.py", "content": "bad"},
                        ),
                    ),
                )
            return LLMResponse(
                content='{"status":"completed","summary":"done","files_modified":[]}'
            )

    provider = ChildProvider()
    guard = SpawnGuard(
        owned=(OwnedPath(str((tmp_path / "owned.py").resolve()), ""),),
        readonly=(),
        base=tmp_path,
    )
    registry = DefaultToolRegistry()
    registry.register(WriteFileTool())
    spec = AgentSpec(
        id="child",
        name="Child",
        capabilities=("write code",),
        prompt_ref="child",
        output_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "summary": {"type": "string"},
                "files_modified": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "summary", "files_modified"],
            "additionalProperties": False,
        },
        max_turns=2,
        timeout_s=10,
    )
    child = LLMAgent(
        spec=spec,
        system_prompt="child",
        user_template="{task}",
        tools=[WriteFileTool()],
        provider=provider,
        tool_registry=registry,
        project_root=tmp_path,
        workdir=tmp_path,
        spawn_guard=guard,
        is_child=True,
    )
    run(
        child.execute(
            {"node_id": "child", "task": "write"},
            AgentContext(
                context=ContextBundle(),
                task_id="task-1",
                node_id="child",
                project_root=tmp_path,
                task_state={},
            ),
        )
    )

    assert not (tmp_path / "stolen.py").exists()
    tool_messages = [
        message
        for message in provider.requests[-1].messages
        if message.role is Role.TOOL
    ]
    assert tool_messages
    assert "owned files or regions" in tool_messages[0].content
