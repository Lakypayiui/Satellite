import pytest

from satellite_py.planner import Plan, PlanStep, Planner, _extract_json


def test_extracts_markdown_json():
    assert _extract_json("```json\n{\"steps\": []}\n```") == {"steps": []}


def test_topological_order_and_cycle_detection():
    plan = Plan(steps=[
        PlanStep(3, dependencies=[1], order=2),
        PlanStep(1, order=0),
        PlanStep(2, dependencies=[1], order=1),
    ])
    assert Planner.execution_order(plan) == [1, 2, 0]
    cycle = Plan(steps=[PlanStep(1, dependencies=[0], order=0)])
    with pytest.raises(ValueError, match="dependency cannot be self"):
        Planner().validate(cycle)


def test_validate_rejects_invalid_dependency_and_order():
    with pytest.raises(ValueError, match="out of bounds"):
        Planner().validate(Plan(steps=[PlanStep(1, dependencies=[2], order=0)]))
    with pytest.raises(ValueError, match="invalid order"):
        Planner().validate(Plan(steps=[PlanStep(1, order=1)]))


def test_prompt_contains_goal_and_catalog():
    prompt = Planner.build_prompt("sum numbers", "[1] sum [math.sum]")
    assert "sum numbers" in prompt
    assert "[1] sum" in prompt
    assert "Responde SOLO con JSON" in prompt


class _FakeComplete:
    def __init__(self, text):
        self.text = text

    def complete(self, system_prompt, user_prompt, max_tokens=500):
        return self.text


def test_plan_goal_normalizes_string_agent_ids():
    """LLMs locales suelen emitir agent_id como string; se normaliza a int."""
    planner = Planner(client=_FakeComplete('''{"goal": "sumar", "steps": [
        {"agent_id": "1", "input": {"a": 2, "b": 3}, "description": "s1", "order": 0, "context": {}},
        {"agent_id": 2, "input": {}, "dependencies": ["0"], "description": "s2", "order": 1, "context": {"paths": ["x.py"]}}
    ]}'''))
    plan = planner.plan_goal("sumar", "catalogo")
    assert plan.steps[0].agent_id == 1
    assert isinstance(plan.steps[0].agent_id, int)
    assert plan.steps[1].dependencies == [0]
    assert all(isinstance(d, int) for d in plan.steps[1].dependencies)
    assert plan.steps[1].context == {"paths": ["x.py"]}


def test_plan_goal_accepts_direct_answer():
    """Tareas de lectura: el plan puede traer answer sin pasos."""
    planner = Planner(client=_FakeComplete(
        '{"goal": "explicar", "steps": [], "answer": "Este proyecto es un framework..."}'
    ))
    plan = planner.plan_goal("explica este proyecto", "catalogo")
    assert not plan.steps
    assert "framework" in plan.answer


def test_plan_goal_rejects_empty_plan_without_answer():
    planner = Planner(client=_FakeComplete('{"goal": "x", "steps": []}'))
    with pytest.raises(ValueError, match="plan vacío"):
        planner.plan_goal("x", "catalogo")


def test_plan_goal_skips_steps_with_null_agent_id():
    """Steps con agent_id null (LLM pequeño) se descartan sin reventar."""
    planner = Planner(client=_FakeComplete(
        '{"goal": "x", "steps": [{"agent_id": null, "input": {}}, '
        '{"agent_id": "2", "input": {"a": 1}, "description": "s2", "order": 0}], '
        '"answer": ""}'
    ))
    plan = planner.plan_goal("x", "catalogo")
    assert len(plan.steps) == 1
    assert plan.steps[0].agent_id == 2
