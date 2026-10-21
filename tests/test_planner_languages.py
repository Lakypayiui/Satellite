"""The detected project language is dynamic planner context."""

import asyncio
import json
from pathlib import Path

from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.spec import AgentSpec
from satellite.core.task import Task
from satellite.orchestration.planner import LLMPlanner
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def _catalog() -> DefaultAgentRegistry:
    registry = DefaultAgentRegistry()
    registry.register_spec(
        AgentSpec(
            id="python_auditor",
            name="Python Auditor",
            capabilities=("audit_python",),
            prompt_ref="python_auditor",
        )
    )
    return registry


def _prompts() -> DefaultPromptRegistry:
    prompts = DefaultPromptRegistry()
    prompts.register_pair(
        "orchestrator",
        "SYSTEM\n{output_schema}",
        "T:{task}\nC:{context}\nF:{feedback}",
    )
    return prompts


def test_do_it_adds_a_continuation_hint(tmp_path: Path) -> None:
    catalog = _catalog()
    plan = {
        "type": "plan",
        "steps": [
            {
                "id": "s1",
                "agent_id": "python_auditor",
                "instruction": "inspect the Python code",
                "depends_on": [],
            }
        ],
        "rationale": "continuation",
    }
    fake = FakeLLM(script=[{"content": json.dumps(plan)}])
    planner = LLMPlanner(fake, catalog, prompts=_prompts())
    task = Task(goal="Hazlo", project_root=tmp_path)
    run(planner.plan(task, catalog))
    dynamic = fake.requests[0].messages[-1].content
    assert "confirming the concrete action" in dynamic
    assert "use a coder agent" in dynamic


def test_language_hint_is_dynamic_and_not_catalog(tmp_path: Path) -> None:
    catalog = _catalog()
    plan = {
        "type": "plan",
        "steps": [
            {
                "id": "s1",
                "agent_id": "python_auditor",
                "instruction": "audit the Python code",
                "depends_on": [],
            }
        ],
        "rationale": "python project",
    }
    fake = FakeLLM(script=[{"content": json.dumps(plan)}])
    planner = LLMPlanner(fake, catalog, prompts=_prompts())
    task = Task(
        goal="audit the code",
        project_root=tmp_path,
        metadata={"project_languages": "Python: 42 files (70.0%)"},
    )
    run(planner.plan(task, catalog))
    request = fake.requests[0]
    dynamic = request.messages[-1].content
    stable_catalog = request.messages[1].content
    assert "Detected project languages" in dynamic
    assert "Python: 42 files" in dynamic
    assert "Python: 42 files" not in stable_catalog
