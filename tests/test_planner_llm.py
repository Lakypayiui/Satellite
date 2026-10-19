"""Tests del LLMPlanner con la salida del agente `orchestrator` (Fase 5)."""

import asyncio
import json
from pathlib import Path

import pytest

from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.errors import ContractError
from satellite.core.spec import AgentSpec
from satellite.core.task import Task
from satellite.orchestration.planner import LLMPlanner
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def make_catalog() -> DefaultAgentRegistry:
    reg = DefaultAgentRegistry()
    reg.register_spec(AgentSpec(
        id="html_writer", name="HTML Writer", capabilities=("write_html",),
        prompt_ref="html_writer", tools=("write_file",),
    ))
    reg.register_spec(AgentSpec(
        id="html_reviewer", name="HTML Reviewer", capabilities=("review_html",),
        prompt_ref="html_reviewer", tools=("read_file",),
    ))
    return reg


def make_prompts() -> DefaultPromptRegistry:
    reg = DefaultPromptRegistry()
    reg.register_pair(
        "orchestrator",
        "SYSTEM estático. Schema:\n{output_schema}",
        "T:{task}\nC:{context}\nF:{feedback}",
    )
    return reg


def make_task(tmp_path: Path) -> Task:
    return Task(goal="crear y verificar una página HTML", project_root=tmp_path)


VALID_PLAN = {
    "type": "plan",
    "steps": [
        {"id": "s1", "agent_id": "html_writer", "instruction": "escribe index.html", "depends_on": []},
        {"id": "s2", "agent_id": "html_reviewer", "instruction": "revisa index.html", "depends_on": ["s1"]},
    ],
    "rationale": "escribir y luego revisar",
}


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def test_plan_builds_graph(tmp_path: Path) -> None:
    catalog = make_catalog()
    fake = FakeLLM(script=[{"content": _json(VALID_PLAN)}])
    planner = LLMPlanner(fake, catalog, prompts=make_prompts())
    result = run(planner.plan(make_task(tmp_path), catalog))
    assert result.kind == "plan"
    assert result.graph is not None
    assert result.graph.entry_node == "s1"
    assert result.graph.nodes["s2"].spec_id == "html_reviewer"
    assert len(result.graph.edges) == 1


def test_plan_renders_prompt_placeholders(tmp_path: Path) -> None:
    catalog = make_catalog()
    fake = FakeLLM(script=[{"content": _json(VALID_PLAN)}])
    planner = LLMPlanner(fake, catalog, prompts=make_prompts())
    run(planner.plan(make_task(tmp_path), catalog))
    request = fake.requests[0]
    system = request.messages[0].content
    # el system es estático: sin placeholders dinámicos ni el goal
    assert "{task}" not in system
    assert "crear y verificar una página HTML" not in system
    # el catálogo va como bloque USER estable y el goal en el USER dinámico
    assert request.stable_user_count == 1
    assert "html_writer" in request.messages[1].content
    assert "crear y verificar una página HTML" in request.messages[-1].content


def test_capability_gap(tmp_path: Path) -> None:
    catalog = make_catalog()
    gap = {
        "type": "capability_gap",
        "missing": "subir archivos por FTP",
        "steps_blocked": ["s2"],
        "partial_plan": [{"id": "s1", "agent_id": "html_writer", "instruction": "x", "depends_on": []}],
        "rationale": "nadie sube FTP",
    }
    planner = LLMPlanner(FakeLLM(script=[{"content": _json(gap)}]), catalog, prompts=make_prompts())
    result = run(planner.plan(make_task(tmp_path), catalog))
    assert result.kind == "capability_gap"
    assert result.gap is not None
    assert result.gap.missing == "subir archivos por FTP"
    assert result.graph is None


def test_needs_clarification(tmp_path: Path) -> None:
    catalog = make_catalog()
    clar = {"type": "needs_clarification", "question": "¿Qué framework usas?"}
    planner = LLMPlanner(FakeLLM(script=[{"content": _json(clar)}]), catalog, prompts=make_prompts())
    result = run(planner.plan(make_task(tmp_path), catalog))
    assert result.kind == "needs_clarification"
    assert result.question == "¿Qué framework usas?"


def test_corrupt_json_retries_then_ok(tmp_path: Path) -> None:
    catalog = make_catalog()
    fake = FakeLLM(script=[{"content": "no es json"}, {"content": _json(VALID_PLAN)}])
    planner = LLMPlanner(fake, catalog, prompts=make_prompts())
    result = run(planner.plan(make_task(tmp_path), catalog))
    assert result.kind == "plan"
    assert len(fake.requests) == 2
    # el segundo intento recibe el error como feedback (en el mensaje dinámico)
    assert "JSON" in fake.requests[1].messages[-1].content


def test_malformed_plan_retries_instead_of_crashing(tmp_path: Path) -> None:
    """A model response missing required keys is a retry, not a task-wide crash."""
    catalog = make_catalog()
    malformed = {"type": "plan", "steps": [{"id": "s1"}]}
    fake = FakeLLM(
        script=[{"content": _json(malformed)}, {"content": _json(VALID_PLAN)}]
    )
    planner = LLMPlanner(fake, catalog, prompts=make_prompts())

    result = run(planner.plan(make_task(tmp_path), catalog))

    assert result.kind == "plan"
    assert len(fake.requests) == 2


def test_corrupt_json_exhausted_raises(tmp_path: Path) -> None:
    catalog = make_catalog()
    fake = FakeLLM(script=[{"content": "nada"}, {"content": "nada"}], default_content="nada")
    planner = LLMPlanner(fake, catalog, prompts=make_prompts(), max_attempts=2)
    with pytest.raises(ContractError):
        run(planner.plan(make_task(tmp_path), catalog))


def test_unknown_agent_id_raises(tmp_path: Path) -> None:
    catalog = make_catalog()
    bad = json.loads(_json(VALID_PLAN))
    bad["steps"][0]["agent_id"] = "no_existe"
    planner = LLMPlanner(FakeLLM(script=[{"content": _json(bad)}]), catalog, prompts=make_prompts())
    with pytest.raises(ContractError):
        run(planner.plan(make_task(tmp_path), catalog))


def test_invalid_type_raises(tmp_path: Path) -> None:
    catalog = make_catalog()
    planner = LLMPlanner(
        FakeLLM(script=[{"content": _json({"type": "what"})}]), catalog, prompts=make_prompts()
    )
    with pytest.raises(ContractError):
        run(planner.plan(make_task(tmp_path), catalog))


def test_deterministic(tmp_path: Path) -> None:
    catalog = make_catalog()
    content = _json(VALID_PLAN)
    p1 = LLMPlanner(FakeLLM(script=[{"content": content}]), catalog, prompts=make_prompts())
    p2 = LLMPlanner(FakeLLM(script=[{"content": content}]), catalog, prompts=make_prompts())
    r1 = run(p1.plan(make_task(tmp_path), catalog))
    r2 = run(p2.plan(make_task(tmp_path), catalog))
    assert r1.graph.to_dict() == r2.graph.to_dict()
