import satellite_py.orchestrator as orchestrator
from satellite_py.planner import Plan, PlanStep
from satellite_py.registry import AgentDescriptor, AgentRegistry
from satellite_py.security import SecurityPolicy


def test_run_plan_topological_order(monkeypatch):
    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="first", capabilities=["x"], library_path="a"))
    registry.register_agent(AgentDescriptor(id=2, name="second", capabilities=["x"], library_path="b"))
    calls = []
    monkeypatch.setattr(orchestrator, "dispatch", lambda registry, security, request, agent_host_bin: calls.append(request["agent_id"]) or {"status": "SUCCESS"})
    result = orchestrator.run_plan(registry, SecurityPolicy({"x": True}), Plan([
        PlanStep(2, dependencies=[1], order=1), PlanStep(1, order=0)
    ]))
    assert result["ok"]
    assert calls == [1, 2]


def test_run_plan_aborts_on_failed_dependency(monkeypatch):
    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="bad", capabilities=["x"], library_path="a"))
    registry.register_agent(AgentDescriptor(id=2, name="next", capabilities=["x"], library_path="b"))
    monkeypatch.setattr(orchestrator, "dispatch", lambda *args, **kwargs: {"status": "FAILED"})
    result = orchestrator.run_plan(registry, SecurityPolicy({"x": True}), Plan([
        PlanStep(1, order=0), PlanStep(2, dependencies=[0], order=1)
    ]))
    assert not result["ok"]
    assert result["results"][0]["status"] == "FAILED"
