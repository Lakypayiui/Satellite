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
