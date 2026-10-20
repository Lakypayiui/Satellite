"""Tests de DefaultAgentFactory."""

import json
from pathlib import Path

from satellite.agents.factory import DefaultAgentFactory
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.runtime import LLMAgent
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.context_tool import RequestContextTool
from satellite.tools.fs import ReadFileTool, WriteFileTool
from satellite.tools.registry import DefaultToolRegistry
from satellite.tools.spawn_agent import SpawnAgentTool


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


def test_factory_passes_the_skill_registry_to_agents(tmp_path: Path) -> None:
    reg = DefaultAgentRegistry()
    prompt_reg = DefaultPromptRegistry()
    spec = {
        "id": "special",
        "name": "Special",
        "description": "x",
        "capabilities": ["x"],
        "prompt_ref": "special",
        "tools": [],
    }
    reg.load_dir(_write_spec(tmp_path, spec))
    prompt_reg.register_pair("special", "P", "Task: {task}")
    sentinel = object()

    factory = DefaultAgentFactory(
        registry=reg,
        prompts=prompt_reg,
        tools=DefaultToolRegistry(),
        provider=FakeLLM(),
        project_root=tmp_path,
        skills=sentinel,
    )

    assert factory.create("special").skills is sentinel


def test_create_uses_the_provider_configured_for_the_agent_role(tmp_path: Path) -> None:
    """A node agent must use `llm.agents`, not always the global provider."""
    reg = DefaultAgentRegistry()
    prompt_reg = DefaultPromptRegistry()
    spec = {
        "id": "special",
        "name": "Special",
        "description": "x",
        "capabilities": ["x"],
        "prompt_ref": "special",
        "tools": [],
        "model_role": "agent",
    }
    reg.load_dir(_write_spec(tmp_path, spec))
    prompt_reg.register_pair("special", "P", "Task: {task}")
    tools_reg = DefaultToolRegistry()
    default = FakeLLM(default_content="global")
    role = FakeLLM(default_content="role")
    seen: list[str] = []

    def resolver(role_key: str):
        seen.append(role_key)
        return role if role_key == "agents" else default

    factory = DefaultAgentFactory(
        registry=reg,
        prompts=prompt_reg,
        tools=tools_reg,
        provider=default,
        project_root=tmp_path,
        provider_resolver=resolver,
    )

    agent = factory.create("special")
    assert seen == ["agents"]
    assert agent.provider is role


def test_specialized_agents_get_spawn_agent_but_reserved_agents_do_not(
    tmp_path: Path,
) -> None:
    reg = DefaultAgentRegistry()
    prompt_reg = DefaultPromptRegistry()
    for spec_id in ("special", "orchestrator"):
        base = tmp_path / spec_id
        base.mkdir()
        reg.load_dir(
            _write_spec(
                base,
                {
                    "id": spec_id,
                    "name": spec_id,
                    "description": "x",
                    "capabilities": ["x"],
                    "prompt_ref": spec_id,
                    "tools": [],
                },
            )
        )
        prompt_reg.register_pair(spec_id, "P", "Task: {task}")

    tools_reg = DefaultToolRegistry()
    tools_reg.register(SpawnAgentTool())
    factory = DefaultAgentFactory(
        registry=reg,
        prompts=prompt_reg,
        tools=tools_reg,
        provider=FakeLLM(),
        project_root=tmp_path,
    )

    special = factory.create("special")
    reserved = factory.create("orchestrator")
    assert "spawn_agent" in {tool.id for tool in special.tools}
    assert "spawn_agent" not in {tool.id for tool in reserved.tools}


def test_factory_deduplicates_tools(tmp_path: Path) -> None:
    """A repeated function name is rejected by OpenAI-compatible providers."""
    reg = DefaultAgentRegistry()
    prompt_reg = DefaultPromptRegistry()
    spec = {
        "id": "special",
        "name": "Special",
        "description": "x",
        "capabilities": ["x"],
        "prompt_ref": "special",
        "tools": ["read_file", "request_context", "request_context"],
    }
    reg.load_dir(_write_spec(tmp_path, spec))
    prompt_reg.register_pair("special", "P", "Task: {task}")
    tools_reg = DefaultToolRegistry()
    tools_reg.register(ReadFileTool())
    tools_reg.register(RequestContextTool())
    tools_reg.register(SpawnAgentTool())

    factory = DefaultAgentFactory(
        registry=reg,
        prompts=prompt_reg,
        tools=tools_reg,
        provider=FakeLLM(),
        project_root=tmp_path,
    )

    agent = factory.create("special")
    ids = [tool.id for tool in agent.tools]
    assert len(ids) == len(set(ids))
    assert ids.count("request_context") == 1


def test_factory_passes_the_subagent_provider_to_parents(tmp_path: Path) -> None:
    reg = DefaultAgentRegistry()
    prompt_reg = DefaultPromptRegistry()
    spec = {
        "id": "special",
        "name": "Special",
        "description": "x",
        "capabilities": ["x"],
        "prompt_ref": "special",
        "tools": [],
    }
    reg.load_dir(_write_spec(tmp_path, spec))
    prompt_reg.register_pair("special", "P", "Task: {task}")
    child_provider = FakeLLM(default_content="child")

    factory = DefaultAgentFactory(
        registry=reg,
        prompts=prompt_reg,
        tools=DefaultToolRegistry(),
        provider=FakeLLM(),
        project_root=tmp_path,
        subagent_provider=child_provider,
    )

    assert factory.create("special").spawn_child_provider is child_provider


def _write_spec(tmp_path: Path, spec: dict) -> Path:
    d = tmp_path / "specs"
    d.mkdir(exist_ok=True)
    (d / f"{spec['id']}.json").write_text(json.dumps(spec), encoding="utf-8")
    return d
