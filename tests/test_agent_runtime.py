"""Tests de LLMAgent con FakeLLM (determinista)."""

import asyncio
from pathlib import Path

from satellite.agents.base import AgentContext
from satellite.agents.runtime import LLMAgent
from satellite.core.result import AgentStatus
from satellite.core.spec import AgentSpec
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import Tool, ToolContext
from satellite.tools.fs import WriteFileTool
from satellite.tools.registry import DefaultToolRegistry
from satellite.core.tool_models import ToolResult


def run(coro):
    return asyncio.run(coro)


def make_spec(output_schema=None, max_turns=1) -> AgentSpec:
    return AgentSpec(
        id="html_writer",
        name="HTML Writer",
        capabilities=("write_html",),
        prompt_ref="html_writer",
        tools=("write_file",),
        output_schema=output_schema,
        max_turns=max_turns,
    )


def make_ctx(tmp_path: Path, feedback=()) -> AgentContext:
    return AgentContext(feedback=feedback)


def make_agent(spec, script, tmp_path: Path, tools=None) -> tuple[LLMAgent, FakeLLM]:
    provider = FakeLLM(script=script)
    registry = DefaultToolRegistry(policy=DefaultSecurityPolicy(allowed_tools={"write_file", "request_context"}))
    registry.register(WriteFileTool())
    agent = LLMAgent(
        spec=spec,
        system_prompt="Eres un escritor.\n{tools}\n{output_schema}",
        user_template="Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
        tools=registry.list_for(spec.tools),
        provider=provider,
        tool_registry=registry,
        policy=DefaultSecurityPolicy(allowed_tools={"write_file", "request_context"}),
        project_root=tmp_path,
    )
    return agent, provider


def test_write_file_and_synthesize(tmp_path: Path) -> None:
    spec = make_spec(output_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["path"],
    })
    script = [
        {"tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "out.html", "content": "<p>hola</p>"}}]},
        {"content": "{\"path\": \"out.html\", \"summary\": \"creado\"}"},
    ]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1", "goal": "crear html"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    assert (tmp_path / "out.html").exists()
    assert result.output == {"path": "out.html", "summary": "creado"}
    assert result.tool_calls[0].tool_name == "write_file"
    assert result.tool_calls[0].success is True


def test_output_schema_valid_json(tmp_path: Path) -> None:
    spec = make_spec(output_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["path", "summary"],
    })
    script = [{"content": "{\"path\": \"a.html\", \"summary\": \"ok\"}"}]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    assert result.output == {"path": "a.html", "summary": "ok"}


def test_output_schema_invalid_retries_then_fails(tmp_path: Path) -> None:
    spec = make_spec(output_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    })
    script = [
        {"content": "{\"sin_path\": 1}"},
        {"content": "{\"sin_path\": 2}"},  # reintento también malo → FAILED
    ]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.FAILED
    assert result.error is not None
    assert "schema" in result.error.lower() or "JSON" in result.error


def test_max_turns_respected(tmp_path: Path) -> None:
    spec = make_spec(max_turns=2)
    script = [
        {"tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}]},
        {"tool_calls": [{"id": "c2", "name": "write_file", "arguments": {"path": "b.txt", "content": "y"}}]},
        {"content": "{\"summary\": \"fin\"}"},
    ]
    agent, provider = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    # 2 rondas de tools + 1 síntesis = 3 llamadas
    assert len(provider.requests) == 3


def test_simple_no_tools(tmp_path: Path) -> None:
    spec = make_spec()
    script = [{"content": "{\"summary\": \"sin tools\"}"}]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    assert result.tool_calls == ()
