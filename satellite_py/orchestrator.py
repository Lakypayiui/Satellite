"""Plan execution for the Python Satellite runtime."""

from typing import Any

from .dispatcher import dispatch
from .planner import Plan, Planner
from .registry import AgentRegistry
from .security import SecurityPolicy


def run_plan(
    registry: AgentRegistry,
    security: SecurityPolicy,
    plan: Plan,
    agent_host_bin: str = "./build/satellite_agent_host",
) -> dict[str, Any]:
    """Execute a validated plan in topological order, stopping at first failure."""
    planner = Planner()
    try:
        order = planner.execution_order(plan)
    except ValueError as error:
        return {"ok": False, "results": [], "summary": str(error)}

    results: dict[int, dict[str, Any]] = {}
    summary: list[str] = []
    for index in order:
        step = plan.steps[index]
        failed_dependency = next(
            (
                dependency
                for dependency in step.dependencies
                if results.get(dependency, {}).get("status") != "SUCCESS"
            ),
            None,
        )
        if failed_dependency is not None:
            result = {
                "agent_id": step.agent_id,
                "status": "FAILED",
                "error": "dependency failed",
            }
        else:
            request = {
                "agent_id": step.agent_id,
                "input": step.input,
                "context": {},
                "metadata": {},
            }
            result = dispatch(registry, security, request, agent_host_bin)

        results[index] = result
        descriptor = registry.find_agent(step.agent_id)
        name = descriptor.name if descriptor else f"id_{step.agent_id}"
        summary.append(f"paso {index}: agent {name} -> {result.get('status')}")
        if result.get("status") != "SUCCESS":
            return {
                "ok": False,
                "results": [results[item] for item in order if item in results],
                "summary": "\n".join(summary),
            }

    return {
        "ok": True,
        "results": [results[item] for item in order],
        "summary": "\n".join(summary),
    }


def execute_goal(
    registry: AgentRegistry,
    security: SecurityPolicy,
    goal: str,
    catalog_prompt: str,
    planner: Planner,
    agent_host_bin: str = "./build/satellite_agent_host",
) -> dict[str, Any]:
    """Plan a goal with the LLM and execute it through the dispatcher."""
    try:
        plan = planner.plan_goal(goal, catalog_prompt)
    except (RuntimeError, ValueError, KeyError) as error:
        return {"ok": False, "results": [], "summary": str(error)}
    return run_plan(registry, security, plan, agent_host_bin)
