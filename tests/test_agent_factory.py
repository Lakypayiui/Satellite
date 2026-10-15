"""Tests de DefaultAgentFactory."""

import json
from pathlib import Path

from satellite.agents.factory import DefaultAgentFactory
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.runtime import LLMAgent
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.fs import ReadFileTool, WriteFileTool
from satellite.tools.registry import DefaultToolRegistry


def test_create_llm_agent_with_filtered_tools(tmp_path: Path) -> None:
    reg = DefaultAgentRegistry()
    prompt_reg = DefaultPromptRegistry()
    spec_id = "html_writer"
    spec = {
        "id": spec_id,
        "name": "HTML Writer",
        "description": "Escribe HTML",
        "capabilities": ["write_html"],
        "prompt_ref": spec_id,
        "tools": ["read_file", "write_file"],
    }
    reg.load_dir(_write_spec(tmp_path, spec))
    prompt_reg.register_pair(spec_id, "Plantilla {tools}", "Tarea: {task}")

    tools_reg = DefaultToolRegistry(policy=DefaultSecurityPolicy(allowed_tools={"read_file", "write_file"}))
    tools_reg.register(ReadFileTool())
    tools_reg.register(WriteFileTool())

    factory = DefaultAgentFactory(
        registry=reg,
        prompts=prompt_reg,
        tools=tools_reg,
        provider=FakeLLM(),
        policy=DefaultSecurityPolicy(allowed_tools={"read_file", "write_file"}),
        project_root=tmp_path,
    )
    agent = factory.create(spec_id)
    assert isinstance(agent, LLMAgent)
    # spec.tools + request_context (si el policy la permite)
    assert {t.id for t in agent.tools} == {"read_file", "write_file", "request_context"}


def test_create_adds_request_context_always(tmp_path: Path) -> None:
    reg = DefaultAgentRegistry()
    prompt_reg = DefaultPromptRegistry()
    spec_id = "special"
    spec = {
        "id": spec_id,
        "name": "Special",
        "description": "x",
        "capabilities": ["x"],
        "prompt_ref": spec_id,
        "tools": [],
    }
    reg.load_dir(_write_spec(tmp_path, spec))
    prompt_reg.register_pair(spec_id, "P", "Tarea: {task}")
    tools_reg = DefaultToolRegistry(policy=DefaultSecurityPolicy(allowed_tools={"request_context"}))
    factory = DefaultAgentFactory(
        registry=reg,
        prompts=prompt_reg,
        tools=tools_reg,
        provider=FakeLLM(),
        policy=DefaultSecurityPolicy(allowed_tools={"request_context"}),
        project_root=tmp_path,
    )
    agent = factory.create(spec_id)
    ids = {t.id for t in agent.tools}
    assert "request_context" in ids


def _write_spec(tmp_path: Path, spec: dict) -> Path:
    d = tmp_path / "specs"
    d.mkdir(exist_ok=True)
    (d / f"{spec['id']}.json").write_text(json.dumps(spec), encoding="utf-8")
    return d
