"""Tests de DefaultToolRegistry."""

import asyncio
from pathlib import Path

import pytest

from satellite.core.errors import ToolError
from satellite.core.tool_models import ToolCall, ToolResult
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import Tool, ToolContext
from satellite.tools.registry import DefaultToolRegistry


def run(coro):
    return asyncio.run(coro)


class StubTool(Tool):
    def __init__(self, tool_id: str, fail: bool = False) -> None:
        self._id = tool_id
        self._fail = fail

    @property
    def id(self) -> str:
        return self._id

    @property
    def description(self) -> str:
        return f"tool {self._id}"

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        if self._fail:
            raise ToolError(f"fallo {self._id}")
        return ToolResult(call_id="c", success=True, output=f"ok {self._id}")


def make_ctx(tmp_path: Path, policy=None) -> ToolContext:
    return ToolContext(project_root=tmp_path, policy=policy)


def test_register_get(tmp_path: Path) -> None:
    reg = DefaultToolRegistry()
    reg.register(StubTool("a"))
    assert reg.get("a").id == "a"
    with pytest.raises(ToolError):
        reg.get("no_existe")


def test_register_duplicate_raises() -> None:
    reg = DefaultToolRegistry()
    reg.register(StubTool("a"))
    with pytest.raises(ToolError):
        reg.register(StubTool("a"))


def test_list_for_and_schemas_for() -> None:
    reg = DefaultToolRegistry()
    reg.register(StubTool("a"))
    reg.register(StubTool("b"))
    tools = reg.list_for(["a", "b"])
    assert [t.id for t in tools] == ["a", "b"]
    schemas = reg.schemas_for(["a"])
    assert schemas[0].name == "a"
    with pytest.raises(ToolError):
        reg.list_for(["a", "zzz"])


def test_execute_policy_denies_returns_result(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(allowed_tools=set())
    reg = DefaultToolRegistry(policy=policy)
    reg.register(StubTool("a"))
    result = run(reg.execute(ToolCall(id="c1", name="a", arguments={}), make_ctx(tmp_path, policy)))
    assert result.success is False
    assert "permiso denegado" in (result.error or "")


def test_execute_tool_error_captured(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(allowed_tools={"a"})
    reg = DefaultToolRegistry(policy=policy)
    reg.register(StubTool("a", fail=True))
    result = run(reg.execute(ToolCall(id="c1", name="a", arguments={}), make_ctx(tmp_path, policy)))
    assert result.success is False
    assert "fallo" in (result.error or "")


def test_execute_success(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(allowed_tools={"a"})
    reg = DefaultToolRegistry(policy=policy)
    reg.register(StubTool("a"))
    result = run(reg.execute(ToolCall(id="c1", name="a", arguments={}), make_ctx(tmp_path, policy)))
    assert result.success is True
    assert result.output == "ok a"
