import os

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


def test_dispatch_so_host_exception_returns_failed(monkeypatch):
    """La rama .so (host) no debe tumbar el run: excepción → resultado FAILED."""
    registry = setup_agent({"type": "object", "required": ["a"]})
    security = SecurityPolicy({"math.sum": True})

    def boom(*args, **kwargs):
        raise FileNotFoundError("satellite_agent_host no encontrado")

    monkeypatch.setattr(dispatcher.agent_host_bridge, "run_agent", boom)
    result = dispatcher.dispatch(registry, security, {"agent_id": 1, "input": {"a": 1}})
    assert result["status"] == "FAILED"
    assert "satellite_agent_host no encontrado" in result["error"]


def test_dispatch_native_without_library_path_delegates_to_cpp(monkeypatch):
    from satellite_py.registry import AgentDescriptor

    registry = AgentRegistry()
    # Nativo 1-5 sin library_path (como lo deja el init C++).
    registry.register_agent(AgentDescriptor(id=1, name="sum", capabilities=["math.sum"], input_schema={}, output_schema={}))
    security = SecurityPolicy({"math.sum": True})
    calls = []

    def fake_cpp(args, request, timeout=300):
        calls.append((args, request))
        return {"ok": True, "output": {"result": 5.0}}

    # El dispatcher importa available/run_cpp_json del bridge al momento de la
    # llamada; monkeypatcheamos el módulo bridge.
    monkeypatch.setattr("satellite_py.runtime.cpp_cli_bridge.available", lambda: True)
    monkeypatch.setattr("satellite_py.runtime.cpp_cli_bridge.run_cpp_json", fake_cpp)
    result = dispatcher.dispatch(registry, security, {"agent_id": 1, "input": {"a": 2, "b": 3}})
    assert result["status"] == "SUCCESS"
    assert result["output"]["result"] == 5.0
    assert calls and calls[0][0] == ["dispatch-step"]


def test_dispatch_native_without_library_path_fails_without_cpp(monkeypatch):
    from satellite_py.registry import AgentDescriptor

    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="sum", capabilities=["math.sum"], input_schema={}, output_schema={}))
    security = SecurityPolicy({"math.sum": True})
    monkeypatch.setattr("satellite_py.runtime.cpp_cli_bridge.available", lambda: False)
    result = dispatcher.dispatch(registry, security, {"agent_id": 1, "input": {"a": 2, "b": 3}})
    assert result["status"] == "FAILED"
    assert "binario C++" in result["error"]


def test_dispatch_native_not_in_registry_delegates_to_cpp(monkeypatch):
    """Registry vacío (init Python): el nativo 1-5 se delega al C++ igual."""
    registry = AgentRegistry()  # sin descriptores
    security = SecurityPolicy()
    calls = []

    def fake_cpp(args, request, timeout=300):
        calls.append((args, request))
        return {"ok": True, "output": {"result": 5.0}}

    monkeypatch.setattr("satellite_py.runtime.cpp_cli_bridge.available", lambda: True)
    monkeypatch.setattr("satellite_py.runtime.cpp_cli_bridge.run_cpp_json", fake_cpp)
    result = dispatcher.dispatch(registry, security, {"agent_id": 1, "input": {"a": 2, "b": 3}})
    assert result["status"] == "SUCCESS"
    assert result["output"]["result"] == 5.0
    assert calls[0][0] == ["dispatch-step"]
    assert calls[0][1]["agent_id"] == 1

    # Ids fuera del rango nativo sin descriptor siguen siendo UNKNOWN_AGENT.
    result = dispatcher.dispatch(registry, security, {"agent_id": 9})
    assert result["status"] == "UNKNOWN_AGENT"


def test_dispatch_auto_expand_creates_and_retries(monkeypatch):
    """auto_expand=True: agente inexistente se crea y el paso se reintenta."""
    registry = AgentRegistry()  # vacío
    security = SecurityPolicy({"math.sum": True})
    created = AgentDescriptor(
        id=7, name="sum_agent", capabilities=["math.sum"],
        library_path="agent7.dll",
        input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]},
        output_schema={"type": "object", "properties": {"result": {"type": "number"}}, "required": ["result"]},
    )
    monkeypatch.setattr("satellite_py.expander.AgentExpander.expand",
                        lambda self, goal, capability: (registry.register_agent(created), created)[1])
    calls = []
    monkeypatch.setattr(dispatcher.agent_host_bridge, "run_agent",
                        lambda *args, **kwargs: calls.append(args[0]) or {"status": "SUCCESS", "output": {"result": 5}})

    result = dispatcher.dispatch(registry, security, {"agent_id": 9, "input": {"a": 2, "b": 3}}, auto_expand=True, goal="sumar")
    assert result["status"] == "SUCCESS"
    assert result["output"]["result"] == 5
    assert registry.find_agent(7) is not None
    # el host se llamó con la library del agente creado
    assert calls == ["agent7.dll"]


def test_dispatch_auto_expand_failure_returns_expansion_failed(monkeypatch):
    registry = AgentRegistry()
    security = SecurityPolicy()

    def boom(self, goal, capability):
        raise RuntimeError("DEEPSEEK_API_KEY no configurada")

    monkeypatch.setattr("satellite_py.expander.AgentExpander.expand", boom)
    result = dispatcher.dispatch(registry, security, {"agent_id": 9, "input": {}}, auto_expand=True, goal="x")
    assert result["status"] == "EXPANSION_FAILED"
    assert "DEEPSEEK_API_KEY" in result["error"]


def test_dispatch_auto_expand_default_off_returns_unknown(monkeypatch):
    """Por defecto auto_expand=False: comportamiento previo (UNKNOWN_AGENT)."""
    registry = AgentRegistry()
    security = SecurityPolicy()
    called = []
    monkeypatch.setattr("satellite_py.expander.AgentExpander.expand",
                        lambda self, goal, capability: called.append(capability))
    result = dispatcher.dispatch(registry, security, {"agent_id": 9, "input": {}})
    assert result["status"] == "UNKNOWN_AGENT"
    assert not called


def test_build_sandbox_respects_security_policy(tmp_path):
    """El sandbox refleja el SecurityPolicy (deny-by-default)."""
    security = SecurityPolicy({"filesystem.write": True, "filesystem.read": True})
    sb = dispatcher._build_sandbox(security, str(tmp_path))
    assert sb["allow_fs_write"] is True
    assert sb["allow_fs_read"] is True
    assert sb["allow_process"] is False  # no permitido
    assert sb["allow_network"] is False
    # El work_dir es el proyecto (no .satellite/workspace): el subagente puede
    # editar el repo. La escritura en .satellite/ queda en deny_write_prefixes.
    assert os.path.normpath(sb["work_dir"]) == os.path.normpath(str(tmp_path))
    assert ".satellite" in sb["deny_write_prefixes"][0]
    assert not sb["deny_write_prefixes"][0].endswith(os.path.join(".satellite", "workspace"))


def test_dispatch_injects_sandbox_for_library_agent(monkeypatch, tmp_path):
    """dispatch con project_root agrega sandbox al request del host."""
    registry = setup_agent({"type": "object", "required": ["a"]})
    security = SecurityPolicy({"math.sum": True, "filesystem.write": True})
    captured = {}

    def fake_run_agent(library_path, request, agent_host_bin="./build/satellite_agent_host"):
        captured["request"] = request
        return {"status": "SUCCESS", "output": {"result": 1}}

    monkeypatch.setattr(dispatcher.agent_host_bridge, "run_agent", fake_run_agent)
    dispatcher.dispatch(registry, security, {"agent_id": 1, "input": {"a": 1}}, project_root=str(tmp_path))
    assert "sandbox" in captured["request"]
    assert captured["request"]["sandbox"]["allow_fs_write"] is True
    assert os.path.normpath(captured["request"]["sandbox"]["work_dir"]) == os.path.normpath(str(tmp_path))
    assert captured["request"]["sandbox"]["deny_write_prefixes"]
