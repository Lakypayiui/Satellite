"""Resultados de varios MB no deben desbordar al piloto ni a los delegados."""

import asyncio
import json

import pytest

from satellite.agents.base import AgentContext
from satellite.agents.runtime import (
    TOOL_HISTORY_CHARS,
    TOOL_MESSAGE_CHARS,
    LLMAgent,
    _bound_tool_messages,
)
from satellite.core.messages import Message, Role
from satellite.core.result import AgentStatus
from satellite.core.spec import AgentSpec
from satellite.core.tool_models import ToolResult
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import Tool, ToolContext
from satellite.tools.fs import LIST_LIMIT, ListDirTool
from satellite.tools.primitives import SearchFilesTool
from satellite.tools.registry import DefaultToolRegistry


@pytest.mark.parametrize("agent_id", ["pilot", "cpp_auditor", "python_coder"])
def test_large_tool_result_is_bounded_before_the_next_model_call(tmp_path, agent_id):
    class LargeTool(Tool):
        id = "large_result"
        description = "Produce a large result"
        @property
        def parameters_schema(self):
            return {"type": "object", "properties": {}}

        async def execute(self, arguments, tool_ctx):
            return ToolResult(success=True, output="x" * 7_580_000)

    provider = FakeLLM(script=[
        {"tool_calls": [{"id": "c1", "name": "large_result", "arguments": {}}]},
        {"content": '{"summary": "done"}'},
    ])
    tool = LargeTool()
    registry = DefaultToolRegistry(policy=DefaultSecurityPolicy(allowed_tools={tool.id}))
    registry.register(tool)
    agent = LLMAgent(
        spec=AgentSpec(id=agent_id, name=agent_id, capabilities=("audit",),
                       prompt_ref=agent_id, tools=("large_result",), max_turns=2),
        system_prompt="System", user_template="Task: {task}",
        tools=[tool], provider=provider, project_root=tmp_path, tool_registry=registry,
    )
    result = asyncio.run(agent.execute({"node_id": "n1", "goal": "review"}, AgentContext()))

    assert result.status is AgentStatus.OK
    message = next(m for m in provider.requests[1].messages if m.role is Role.TOOL)
    assert len(message.content) <= TOOL_MESSAGE_CHARS
    assert json.loads(message.content)["truncated"] is True
    assert message.tool_call_id == "c1"


def test_many_recent_tool_results_share_one_budget_and_keep_call_ids():
    messages = [Message(role=Role.SYSTEM, content="rules")]
    messages.extend(Message(role=Role.TOOL, content=json.dumps({"text": '"\\' * 40_000}),
                            tool_call_id=f"c{i}") for i in range(20))
    bounded = _bound_tool_messages(messages)

    assert sum(len(m.content) for m in bounded if m.role is Role.TOOL) <= TOOL_HISTORY_CHARS
    assert [m.tool_call_id for m in bounded] == [m.tool_call_id for m in messages]
    assert bounded[0] == messages[0]
    assert all(json.loads(m.content)["truncated"] for m in bounded[1:])
    assert len(bounded[-1].content) > len(bounded[1].content)


def test_broad_filesystem_queries_skip_generated_trees_and_signal_truncation(tmp_path):
    for folder in (".git", ".satellite", "node_modules", "src"):
        directory = tmp_path / folder
        directory.mkdir()
        for i in range(LIST_LIMIT + 2):
            (directory / f"file{i}.cpp").touch()
    ctx = ToolContext(project_root=tmp_path)
    search = asyncio.run(SearchFilesTool().execute({"pattern": "**/*"}, ctx))
    assert search.output["truncated"] is True
    assert len(search.output["files"]) == LIST_LIMIT
    assert all(item["path"].startswith("src/") for item in search.output["files"])

    listing = asyncio.run(ListDirTool().execute({"path": ".", "recursive": True}, ctx))
    assert listing.output["truncated"] is True
    assert len(listing.output["entries"]) == LIST_LIMIT
    assert all("/node_modules/" not in item["path"] and "/.git/" not in item["path"]
               and "/.satellite/" not in item["path"] for item in listing.output["entries"])


def test_recursive_listing_does_not_follow_symlink_cycles(tmp_path):
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    result = asyncio.run(ListDirTool().execute({"path": ".", "recursive": True}, ToolContext(project_root=tmp_path)))
    assert len(result.output) == 1


def test_a_shortened_read_is_not_marked_as_still_complete():
    from satellite.agents.runtime import _ReadLedger
    from satellite.core.tool_models import ToolCall

    ledger = _ReadLedger()
    call = ToolCall(id="c1", name="read_file", arguments={"path": "large.cpp"})
    ledger.remember(call, ordinal=0)
    messages = _bound_tool_messages([
        Message(role=Role.TOOL, tool_call_id="c1", content=json.dumps({"output": "x" * 50_000})),
    ])
    ledger.retain_complete(messages)

    assert not ledger.repeated(call, total=1, keep=None)
