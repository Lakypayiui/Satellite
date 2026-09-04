import satellite_py.dispatcher as dispatcher
from satellite_py.registry import AgentDescriptor, AgentRegistry
from satellite_py.security import SecurityPolicy


def setup_agent(input_schema=None, output_schema=None):
    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(
        id=1,
        name="sum",
        capabilities=["math.sum"],
        library_path="agent.dll",
        input_schema=input_schema or {},
        output_schema=output_schema or {},
    ))
    return registry


def test_dispatch_unknown_and_disabled():
    registry = setup_agent()
    security = SecurityPolicy({"math.sum": True})
    assert dispatcher.dispatch(registry, security, {"agent_id": 9})["status"] == "UNKNOWN_AGENT"
    registry.find_agent(1).enabled = False
    assert dispatcher.dispatch(registry, security, {"agent_id": 1})["status"] == "DISABLED"


def test_dispatch_validates_security_and_input(monkeypatch):
    registry = setup_agent({"type": "object", "required": ["a"]})
    assert dispatcher.dispatch(registry, SecurityPolicy(), {"agent_id": 1, "input": {}})["status"] == "SECURITY_DENIED"
    security = SecurityPolicy({"math.sum": True})
    called = []
    monkeypatch.setattr(dispatcher.agent_host_bridge, "run_agent", lambda *args, **kwargs: called.append(True) or {"status": "SUCCESS", "output": {}})
    result = dispatcher.dispatch(registry, security, {"agent_id": 1, "input": {}})
    assert result["status"] == "VALIDATION_ERROR"
    assert not called


def test_dispatch_validates_output(monkeypatch):
    registry = setup_agent(output_schema={"type": "object", "required": ["result"]})
    security = SecurityPolicy({"math.sum": True})
    monkeypatch.setattr(dispatcher.agent_host_bridge, "run_agent", lambda *args, **kwargs: {"status": "SUCCESS", "output": {}})
    assert dispatcher.dispatch(registry, security, {"agent_id": 1, "input": {}})["status"] == "VALIDATION_ERROR"
