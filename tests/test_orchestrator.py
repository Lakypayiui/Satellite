import satellite_py.orchestrator as orchestrator
from satellite_py.planner import Plan, PlanStep
from satellite_py.registry import AgentDescriptor, AgentRegistry
from satellite_py.security import SecurityPolicy


def test_execute_goal_direct_answer_returns_without_dispatch(monkeypatch, tmp_path):
    """Plan con answer (sin pasos): responde directo, no ejecuta agentes."""
    class _AnswerPlanner:
        def plan_goal(self, goal, catalog_prompt):
            return Plan(goal=goal, answer="Este proyecto es un framework de microagentes.")

    registry = AgentRegistry()
    calls = []
    monkeypatch.setattr(orchestrator, "dispatch", lambda *a, **k: calls.append(1) or {"status": "SUCCESS"})
    result = orchestrator.execute_goal(
        registry, SecurityPolicy(), "explica este proyecto", "cat",
        _AnswerPlanner(),
        project_root=str(tmp_path),
    )
    assert result["ok"] is True
    assert "microagentes" in result["summary"]
    assert result.get("answer") == "Este proyecto es un framework de microagentes."
    assert not calls  # ningún agente se ejecutó


def test_run_plan_topological_order(monkeypatch):
    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="first", capabilities=["x"], library_path="a"))
    registry.register_agent(AgentDescriptor(id=2, name="second", capabilities=["x"], library_path="b"))
    calls = []
    monkeypatch.setattr(orchestrator, "dispatch", lambda registry, security, request, agent_host_bin=None, **kwargs: calls.append(request["agent_id"]) or {"status": "SUCCESS"})
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


def test_run_plan_resolves_per_step_context(monkeypatch):
    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="sum", capabilities=["x"], library_path="a"))
    captured = []

    def fake_dispatch(registry, security, request, agent_host_bin=None, **kwargs):
        captured.append(request["context"])
        return {"status": "SUCCESS", "output": {"result": 5}}

    monkeypatch.setattr(orchestrator, "dispatch", fake_dispatch)

    class FakeResolver:
        def resolve(self, paths=None, symbols=None):
            assert paths == ["math_utils.py"]
            assert symbols == ["add"]
            return "=== math_utils.py ===\ndef add(a, b): ..."

    plan = Plan(
        goal="sumar",
        steps=[PlanStep(1, order=0, description="suma", context={"paths": ["math_utils.py"], "symbols": ["add"]})],
    )
    result = orchestrator.run_plan(
        registry,
        SecurityPolicy({"x": True}),
        plan,
        resolver=FakeResolver(),
        compressor=None,
    )
    assert result["ok"]
    assert captured[0]["project"] == "=== math_utils.py ===\ndef add(a, b): ..."


def test_run_plan_persists_neutral_session(monkeypatch, tmp_path):
    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="sum", capabilities=["x"], library_path="a"))
    monkeypatch.setattr(orchestrator, "dispatch", lambda *a, **k: {"status": "SUCCESS", "output": {"result": 5}})
    session_file = tmp_path / "session_test.json"
    result = orchestrator.run_plan(
        registry,
        SecurityPolicy({"x": True}),
        Plan(goal="sumar", steps=[PlanStep(1, order=0, description="suma")]),
        session_path=str(session_file),
    )
    assert result["ok"]
    import json

    doc = json.loads(session_file.read_text(encoding="utf-8"))
    assert doc["goal"] == "sumar"
    assert doc["provider_agnostic"] is True
    assert doc["steps"][0]["status"] == "SUCCESS"
    assert doc["steps"][0]["output"]["result"] == 5


def _make_resolver_with_index(root, files, mtime_offset=0.0):
    """files: dict relative -> content. Crea índice con mtime real (o corrido)."""
    import json as jsonlib
    import time

    from satellite_py.context import ContextPreprocessor

    class _NeverCalled:
        def complete(self, system_prompt, user_prompt, max_tokens=500):
            raise AssertionError("no se debe llamar al LLM en este test")

    entries = []
    for relative, content in files.items():
        full = root / relative
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        entries.append(
            {
                "path": relative,
                "size": len(content.encode("utf-8")),
                "language": "Python" if relative.endswith(".py") else "C++",
                "symbols": [],
                "mtime": int(time.time()) - mtime_offset,
            }
        )
    index_path = root / ".satellite" / "context" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(jsonlib.dumps({"root": str(root), "files": entries}), encoding="utf-8")
    return ContextPreprocessor(client=_NeverCalled(), project_root=root)


def test_run_plan_refreshes_context_between_steps(tmp_path, monkeypatch):
    import os
    import time

    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="w1", capabilities=["x"], library_path="a"))
    registry.register_agent(AgentDescriptor(id=2, name="w2", capabilities=["x"], library_path="b"))
    captured = []

    def fake_dispatch(registry, security, request, agent_host_bin=None, **kwargs):
        captured.append(request["context"])
        # El paso 0 "modifica" el archivo que el paso 1 va a usar; se fuerza un
        # mtime futuro para que el paso 1 lo detecte como stale de forma fiable.
        if request["agent_id"] == 1:
            target = tmp_path / "data.txt"
            target.write_text("contenido NUEVO del paso 0\n", encoding="utf-8")
            future = time.time() + 5
            os.utime(target, (future, future))
        return {"status": "SUCCESS", "output": {"result": 1}}

    monkeypatch.setattr(orchestrator, "dispatch", fake_dispatch)

    resolver = _make_resolver_with_index(tmp_path, {"data.txt": "contenido ORIGINAL\n"}, mtime_offset=10)
    plan = Plan(
        goal="procesar",
        steps=[
            PlanStep(1, order=0, description="escribe data.txt", context={"paths": ["data.txt"]}),
            PlanStep(2, order=1, description="lee data.txt", context={"paths": ["data.txt"]}),
        ],
    )
    result = orchestrator.run_plan(registry, SecurityPolicy({"x": True}), plan, resolver=resolver)
    assert result["ok"]
    assert len(captured) == 2
    # El paso 1 recibió el contenido NUEVO (refrescado por mtime).
    assert "contenido NUEVO" in captured[1]["project"], captured[1]["project"]
    assert "contexto refrescado" in result["summary"]


def test_run_plan_logs_executions(tmp_path, monkeypatch):
    from satellite_py.execution_log import ExecutionLogger

    registry = AgentRegistry()
    registry.register_agent(AgentDescriptor(id=1, name="sum", capabilities=["x"], library_path="a"))
    monkeypatch.setattr(orchestrator, "dispatch", lambda *a, **k: {"status": "SUCCESS", "output": {"result": 5}})
    logs_dir = tmp_path / "executions"
    logger = ExecutionLogger(str(logs_dir))
    result = orchestrator.run_plan(
        registry,
        SecurityPolicy({"x": True}),
        Plan(goal="sumar", steps=[PlanStep(1, order=0, description="suma")]),
        execution_logger=logger,
    )
    assert result["ok"]
    assert logger.count() == 1
    import json

    files = list(logs_dir.glob("exec_*.json"))
    assert len(files) == 1
    content = json.loads(files[0].read_text(encoding="utf-8"))
    assert content["agent_id"] == 1
    assert content["agent_name"] == "sum"
    assert content["status"] == "SUCCESS"


def _complement_registry():
    """Generador (code.gen) que complementa a compilador (compile)."""
    registry = AgentRegistry()
    registry.register_agent(
        AgentDescriptor(id=1, name="gen", capabilities=["code.gen"], library_path="a",
                        complements=["compile"])
    )
    registry.register_agent(
        AgentDescriptor(id=2, name="compiler", capabilities=["compile"], library_path="b")
    )
    return registry


def test_run_plan_routes_output_directly_to_complement(monkeypatch):
    registry = _complement_registry()
    calls = []

    def fake_dispatch(registry, security, request, agent_host_bin=None, **kwargs):
        calls.append((request["agent_id"], request["input"], request["metadata"]))
        if request["agent_id"] == 1:
            return {"status": "SUCCESS", "output": {"code": "int main(){}", "lang": "cpp"}}
        return {"status": "SUCCESS", "output": {"compiled": True}}

    monkeypatch.setattr(orchestrator, "dispatch", fake_dispatch)
    result = orchestrator.run_plan(
        registry,
        SecurityPolicy({"code.gen": True, "compile": True}),
        Plan(goal="generar y compilar", steps=[PlanStep(1, order=0, description="generar")]),
    )
    assert result["ok"]
    # 2 invocaciones: el generador y el compilador (ruteado directo).
    assert [c[0] for c in calls] == [1, 2]
    # El input del compilador es el OUTPUT del generador.
    assert calls[1][1] == {"code": "int main(){}", "lang": "cpp"}
    assert calls[1][2].get("routed_from") == 0
    assert "paso 0.1" in result["summary"]


def test_run_plan_complement_failure_returns_to_orchestrator(monkeypatch):
    registry = _complement_registry()
    calls = []

    def fake_dispatch(registry, security, request, agent_host_bin=None, **kwargs):
        calls.append(request["agent_id"])
        if request["agent_id"] == 1:
            return {"status": "SUCCESS", "output": {"code": "broken"}}
        return {"status": "FAILED", "error": {"message": "compile error"}, "output": {}}

    monkeypatch.setattr(orchestrator, "dispatch", fake_dispatch)
    result = orchestrator.run_plan(
        registry,
        SecurityPolicy({"code.gen": True, "compile": True}),
        Plan(goal="generar y compilar", steps=[PlanStep(1, order=0, description="generar")]),
    )
    assert not result["ok"]
    assert calls == [1, 2]
    # El resultado del paso refleja el fallo del complemento, marcado para el orquestador.
    assert result["results"][0]["status"] == "FAILED"
    assert result["results"][0].get("routed_to_orchestrator") is True
    assert "compile error" in str(result["results"][0].get("error"))


def test_run_plan_no_complement_match_is_terminal(monkeypatch):
    registry = AgentRegistry()
    registry.register_agent(
        AgentDescriptor(id=1, name="gen", capabilities=["code.gen"], library_path="a",
                        complements=["compile"])
    )
    # Sin agente con capability compile: el paso es hoja.
    calls = []

    def fake_dispatch(registry, security, request, agent_host_bin=None, **kwargs):
        calls.append(request["agent_id"])
        return {"status": "SUCCESS", "output": {"code": "x"}}

    monkeypatch.setattr(orchestrator, "dispatch", fake_dispatch)
    result = orchestrator.run_plan(
        registry,
        SecurityPolicy({"code.gen": True}),
        Plan(goal="generar", steps=[PlanStep(1, order=0, description="generar")]),
    )
    assert result["ok"]
    assert calls == [1]  # solo el generador; sin complemento no encadena


def test_run_plan_auto_expand_creates_missing_agent(monkeypatch):
    """run_plan con auto_expand: el dispatch crea el agente y el paso corre."""
    registry = AgentRegistry()
    calls = []

    def fake_dispatch(registry, security, request, agent_host_bin=None, **kwargs):
        calls.append((request["agent_id"], request.get("metadata"), kwargs.get("auto_expand"), kwargs.get("goal")))
        # El dispatch "crea" el agente 7 (como haría AgentExpander) y responde.
        registry.register_agent(AgentDescriptor(
            id=7, name="nuevo", capabilities=["x"], library_path="nuevo.dll",
            input_schema={}, output_schema={},
        ))
        return {"status": "SUCCESS", "output": {"ok": True}}

    monkeypatch.setattr(orchestrator, "dispatch", fake_dispatch)
    result = orchestrator.run_plan(
        registry,
        SecurityPolicy(),
        Plan(goal="hacer algo", steps=[PlanStep(9, order=0, description="paso con agente faltante")]),
        auto_expand=True,
    )
    assert result["ok"]
    # El dispatch recibió auto_expand=True y el goal de la corrida.
    assert calls and calls[0][2] is True
    assert calls[0][3] == "hacer algo"
    # La description viaja en el metadata para derivar la capability.
    assert calls[0][1] == {"description": "paso con agente faltante"}
