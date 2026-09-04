import pytest

import satellite_py.expander as expander_module
from satellite_py.expander import AgentExpander
from satellite_py.registry import AgentRegistry


def test_expander_registers_factory_descriptor(monkeypatch):
    monkeypatch.setattr(expander_module, "create_agent", lambda goal, capability: {
        "ok": True,
        "descriptor": {
            "id": 7,
            "name": "factorial",
            "description": "factorial",
            "capabilities": [capability],
            "library_path": "agent.dll",
        },
    })
    registry = AgentRegistry()
    descriptor = AgentExpander(registry).expand("calculate factorial", "factorial")
    assert descriptor.id == 7
    assert registry.find_agent(7).library_path == "agent.dll"


def test_expander_propagates_factory_failure(monkeypatch):
    monkeypatch.setattr(expander_module, "create_agent", lambda *args: {"ok": False, "error": "compile_test"})
    with pytest.raises(RuntimeError, match="compile_test"):
        AgentExpander(AgentRegistry()).expand("broken", "broken")


def test_expander_prompt_preserves_contract():
    prompt = AgentExpander.build_prompt("sum", "math.sum")
    assert "Capacidad requerida: math.sum" in prompt
    assert "satellite_create_agent" in prompt
