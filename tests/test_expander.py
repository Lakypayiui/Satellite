import pytest

import satellite_py.expander as expander_module
from satellite_py.expander import AgentExpander
from satellite_py.registry import AgentRegistry


def _spec():
    return {
        "name": "factorial",
        "description": "factorial",
        "version": "1.0.0",
        "input_schema": {"type": "object", "properties": {"n": {"type": "number"}}, "required": ["n"]},
        "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}, "required": ["result"]},
        "implementation_logic": "json in = req.input;\njson out;\nout[\"result\"] = 1;",
        "test_cases": [{"input": {"n": 3}, "expected": {"result": 6}}],
    }


def _fake_factory_ok(payload, cwd=None):
    return {
        "ok": True,
        "descriptor": {
            "id": 7,
            "name": payload["name"],
            "description": payload.get("description", ""),
            "capabilities": ["math.factorial"],
            "library_path": "agent_7.dll",
            "input_schema": payload["input_schema"],
            "output_schema": payload["output_schema"],
        },
    }


def test_expander_generates_spec_and_registers_descriptor(monkeypatch):
    spec = _spec()
    monkeypatch.setattr(expander_module.AgentExpander, "generate_spec", lambda self, goal, capability: dict(spec))
    monkeypatch.setattr(expander_module, "create_agent_from_spec", _fake_factory_ok)
    registry = AgentRegistry()
    descriptor = AgentExpander(registry).expand("calculate factorial", "math.factorial")
    assert descriptor.id == 7
    assert registry.find_agent(7).library_path == "agent_7.dll"
    assert descriptor.capabilities == ["math.factorial"]


def test_expander_wraps_logic_in_abi_template():
    spec = _spec()
    code = AgentExpander.build_implementation_code(spec)
    assert 'class Factorial : public a::IAgent' in code
    assert "satellite_create_agent" in code
    assert "satellite_destroy_agent" in code
    assert 'out["result"]' in code
    assert "AgentStatus::SUCCESS" in code
    # El return out; se elimina (el template asigna result.output = out).
    assert "return out" not in code


def test_expander_effects_template_exposes_sandbox():
    """El template de efectos expone el sandbox y rechaza sin él."""
    spec = _spec()
    code = AgentExpander.build_implementation_code(spec, use_effects=True)
    assert "AgentSandbox.h" in code
    assert "const a::AgentSandbox& sb = *req.sandbox;" in code
    assert "el runtime no habilito efectos de sistema" in code
    # El `sb` queda disponible para la lógica del LLM.
    assert "sb.write_file" not in code  # la lógica la pone el LLM, no el template


def test_expander_effects_prompt_mentions_helpers():
    prompt = AgentExpander.build_prompt("crear archivo", "filesystem.write", use_effects=True)
    assert "sb.write_file" in prompt
    assert "sb.run_process" in prompt
    assert "NUNCA toques" not in prompt  # la restricción está en el system prompt
    assert ".satellite" in prompt.lower() or "proyecto" in prompt.lower()


def test_expander_propagates_factory_failure(monkeypatch):
    spec = _spec()
    monkeypatch.setattr(expander_module.AgentExpander, "generate_spec", lambda self, goal, capability: dict(spec))
    monkeypatch.setattr(expander_module, "create_agent_from_spec", lambda payload, cwd=None: {"ok": False, "error": "compile_test"})
    with pytest.raises(RuntimeError, match="compile_test"):
        AgentExpander(AgentRegistry()).expand("broken", "broken")


def test_expander_generate_spec_uses_configured_client():
    class _FakeClient:
        def __init__(self):
            self.calls = []

        def complete(self, system_prompt, user_prompt, max_tokens=500):
            self.calls.append(user_prompt)
            return (
                '{"name": "sum", "description": "suma", '
                '"input_schema": {"type": "object"}, "output_schema": {}, '
                '"implementation_logic": "json out; out[\\"result\\"] = 1;", '
                '"test_cases": [{"input": {}, "expected": {}}]}'
            )

    client = _FakeClient()
    registry = AgentRegistry()
    expander = AgentExpander(registry, client=client)
    spec = expander.generate_spec("sumar", "math.sum")
    assert spec["name"] == "sum"
    assert client.calls and "math.sum" in client.calls[0]
    assert "implementation_logic" in client.calls[0]


def test_expander_generate_spec_rejects_invalid_llm_output():
    class _FakeClient:
        def complete(self, system_prompt, user_prompt, max_tokens=500):
            return "no json aqui"

    with pytest.raises(RuntimeError, match="spec JSON"):
        AgentExpander(AgentRegistry(), client=_FakeClient()).generate_spec("x", "y")


def test_expander_prompt_preserves_contract():
    prompt = AgentExpander.build_prompt("sum", "math.sum")
    assert "Capacidad requerida: math.sum" in prompt
    assert "implementation_logic" in prompt
    assert "satellite_create_agent" not in prompt  # el template lo añade
