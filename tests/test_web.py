"""Tests for the Satellite web API (satellite_py/web)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from satellite_py.registry import AgentDescriptor
from satellite_py.store import AgentStore
from satellite_py.web import runner
from satellite_py.web.app import app


@pytest.fixture(autouse=True)
def _reset_web_root(monkeypatch):
    """Aísla el estado global del server web entre tests."""
    import satellite_py.web.app as app_module

    app_module._ROOT = None
    monkeypatch.delenv("SATELLITE_WEB_ROOT", raising=False)
    yield
    app_module._ROOT = None


def _write_project(root: Path) -> None:
    """Create a small project with an index so preprocess/resolve can run."""
    (root / "math_utils.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )


def _init_store(root: Path, agents) -> None:
    store = AgentStore(str(root))
    store.initialize()
    for agent in agents:
        store.save_descriptor(agent)
    registry = store.load_registry()
    store.save_registry(registry)


def _config(root: Path, provider: str = "local") -> None:
    path = root / ".satellite" / "config" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "llm": {"provider": provider, "model": "fake"},
            "execution": {"backend": "native_process"},
        }),
        encoding="utf-8",
    )


def test_web_agents_graph_lists_complement_edges(tmp_path, monkeypatch):
    """GET /api/agents returns the complement edges of the registry."""
    _write_project(tmp_path)
    _init_store(
        tmp_path,
        [
            AgentDescriptor(id=1, name="generator", capabilities=["code.gen"], library_path="gen.dll", complements=["compile"]),
            AgentDescriptor(id=2, name="compiler", capabilities=["compile"], library_path="cc.dll"),
            AgentDescriptor(id=3, name="off", capabilities=["x"], library_path="off.dll", enabled=False),
        ],
    )
    _config(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))

    # TestClient directo de fastapi.
    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.get("/api/agents")
        assert resp.status_code == 200
        payload = resp.json()
        ids = {n["id"] for n in payload["nodes"]}
        # 1-3 del registry + los nativos C++ ausentes (4,5) que el grafo
        # expone siempre (viven in-process en el C++).
        assert ids == {1, 2, 3, 4, 5}
        assert any(
            e["source"] == 1 and e["target"] == 2 and e["capability"] == "compile"
            for e in payload["edges"]
        )
        assert not any(e["source"] == 3 for e in payload["edges"])


def test_web_project_switch_and_dirs(tmp_path, monkeypatch):
    """Cambiar la carpeta del proyecto activo y listar directorios."""
    other = tmp_path / "otro_proyecto"
    other.mkdir()
    _write_project(tmp_path)
    _init_store(tmp_path, [AgentDescriptor(id=1, name="a", capabilities=["x"], library_path="a.dll")])
    _config(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        # listar dirs del sistema
        resp = tc.get("/api/dirs", params={"path": str(tmp_path)})
        assert resp.status_code == 200
        names = {e["name"] for e in resp.json()["entries"]}
        assert "otro_proyecto" in names

        # cambiar de proyecto
        resp = tc.post("/api/project/set", json={"path": str(other)})
        assert resp.status_code == 200
        assert resp.json()["root"] == str(other.resolve())
        # el sistema ahora apunta al otro (sin estado .satellite)
        sys = tc.get("/api/system").json()
        assert sys["root"] == str(other.resolve())
        assert sys["initialized"] is False

        # inicializar desde la web
        resp = tc.post("/api/project/init", json={})
        assert resp.status_code == 200
        assert tc.get("/api/system").json()["initialized"] is True


def test_web_agent_block_unblock(tmp_path, monkeypatch):
    _write_project(tmp_path)
    _init_store(
        tmp_path,
        [AgentDescriptor(id=1, name="g", capabilities=["code.gen"], library_path="g.dll")],
    )
    _config(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        # bloquear
        resp = tc.post("/api/agents/1/enabled", json={"enabled": False})
        assert resp.status_code == 200
        resp = tc.get("/api/agents/1")
        assert resp.json()["enabled"] is False
        # desbloquear
        resp = tc.post("/api/agents/1/enabled", json={"enabled": True})
        assert resp.status_code == 200
        assert tc.get("/api/agents/1").json()["enabled"] is True


def test_web_file_read_write(tmp_path, monkeypatch):
    _write_project(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.get("/api/file", params={"path": "math_utils.py"})
        assert resp.status_code == 200
        assert "def add" in resp.json()["content"]

        resp = tc.put("/api/file", json={"path": "nuevo.py", "content": "x = 1\n"})
        assert resp.status_code == 200
        assert (tmp_path / "nuevo.py").read_text(encoding="utf-8") == "x = 1\n"

        # path traversal debe fallar
        resp = tc.get("/api/file", params={"path": "../secret.txt"})
        assert resp.status_code in (403, 404)


def test_web_file_ask_uses_selected_file_as_context(tmp_path, monkeypatch):
    """POST /api/file/ask responde usando el archivo ya seleccionado."""
    _write_project(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))
    _config(tmp_path)

    calls = {}

    class _FakeCfg:
        provider = "local"
        model = "fake"

        def create_client(self):
            class _C:
                def complete(self, system, user, max_tokens=500):
                    calls["prompt"] = user
                    return "La funcion add suma dos numeros."
            return _C()

    monkeypatch.setattr("satellite_py.llm.llm_role_config", lambda data, role: _FakeCfg())

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.post("/api/file/ask", json={"path": "math_utils.py", "question": "¿qué hace add?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "suma" in data["answer"]
        # El contenido del archivo se inyectó en el prompt (contexto).
        assert "def add" in calls["prompt"]
        assert "math_utils.py" in calls["prompt"]


def test_web_auth_required_when_token_set(tmp_path, monkeypatch):
    """Con SATELLITE_WEB_TOKEN, /api exige el token (401 sin él)."""
    _write_project(tmp_path)
    _init_store(tmp_path, [AgentDescriptor(id=1, name="a", capabilities=["x"], library_path="a.dll")])
    _config(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))
    monkeypatch.setenv("SATELLITE_WEB_TOKEN", "s3cret")

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.get("/api/system")
        assert resp.status_code == 401
        resp = tc.get("/api/system", headers={"X-Satellite-Token": "s3cret"})
        assert resp.status_code == 200
        # token incorrecto sigue rechazado
        resp = tc.get("/api/system", headers={"X-Satellite-Token": "nope"})
        assert resp.status_code == 401


def test_web_project_set_confined_to_base(tmp_path, monkeypatch):
    """/api/project/set rechaza rutas fuera del área permitida."""
    _write_project(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))
    outside = tmp_path.parent / "fuera_del_area"
    outside.mkdir(exist_ok=True)

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.post("/api/project/set", json={"path": str(outside)})
        assert resp.status_code == 403
        # dentro del área sí se acepta
        inside = tmp_path / "sub"
        inside.mkdir()
        resp = tc.post("/api/project/set", json={"path": str(inside)})
        assert resp.status_code == 200


def test_web_satellite_meta_and_traversal_blocked(tmp_path, monkeypatch):
    """.satellite/ y escapes del root no son legibles ni escribibles."""
    _write_project(tmp_path)
    (tmp_path / ".satellite" / "config").mkdir(parents=True)
    (tmp_path / ".satellite" / "config" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.get("/api/file", params={"path": ".satellite/config/config.json"})
        assert resp.status_code == 403
        resp = tc.put("/api/file", json={"path": ".satellite/evil.py", "content": "x=1"})
        assert resp.status_code == 403
        assert not (tmp_path / ".satellite" / "evil.py").exists()
        resp = tc.get("/api/file", params={"path": "../secret.txt"})
        assert resp.status_code in (403, 404)


def test_web_run_goal_sanitized_and_rate_limited(tmp_path, monkeypatch):
    """/api/run sanea el goal y aplica rate-limit de runs."""
    import satellite_py.web.app as app_module

    monkeypatch.setattr(app_module, "_run_times", [])
    monkeypatch.setenv("SATELLITE_WEB_MAX_RUNS", "2")

    _write_project(tmp_path)
    _init_store(tmp_path, [AgentDescriptor(id=1, name="a", capabilities=["x"], library_path="a.dll")])
    _config(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))

    sanity = {}

    # Stub del runner para validar el goal saneado sin ejecutar nada.
    def fake_start_run(goal, root=None, resume=None, max_rounds=3):
        sanity["goal"] = goal
        return "fake-run-id"

    monkeypatch.setattr(runner, "start_run", fake_start_run)

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.post("/api/run", json={"goal": "hola\u0000\u0001mundo" * 1000})
        assert resp.status_code == 200
        assert len(sanity["goal"]) <= 4000
        assert "\x00" not in sanity["goal"]
        # segundo run OK, tercero 429
        assert tc.post("/api/run", json={"goal": "otro"}).status_code == 200
        assert tc.post("/api/run", json={"goal": "tercero"}).status_code == 429


class _ScriptedClient:
    """Returns canned responses in order; records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=500):
        self.calls.append(user_prompt)
        return self.responses.pop(0) if self.responses else "{}"


def test_web_run_end_to_end_with_fake_llm(tmp_path, monkeypatch):
    """A run through the API reaches 'done' with the fake dispatch + LLM."""
    _write_project(tmp_path)
    # índice para resolver pasos
    index = tmp_path / ".satellite" / "context" / "index.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        json.dumps({
            "root": str(tmp_path),
            "files": [
                {"path": "math_utils.py", "size": 26, "language": "Python", "symbols": ["add"]}
            ],
        }),
        encoding="utf-8",
    )
    _init_store(
        tmp_path,
        [AgentDescriptor(id=1, name="math", capabilities=["math.sum"], library_path="m.dll")],
    )
    _config(tmp_path)
    monkeypatch.setenv("SATELLITE_WEB_ROOT", str(tmp_path))

    # Orden real de llamadas del runner: preprocess → compressor → planner.
    fake = _ScriptedClient(
        [
            json.dumps({"category": "general", "sufficient": True, "description": "ok"}),
            json.dumps({"intention": "sumar", "constraints": [], "references": {}, "status": ""}),
            json.dumps({
                "goal": "sumar",
                "steps": [
                    {"agent_id": 1, "input": {"a": 2, "b": 3},
                     "dependencies": [], "description": "sumar", "order": 0,
                     "context": {"paths": ["math_utils.py"], "symbols": ["add"]}},
                ],
            }),
        ]
    )

    class _FakeRoleConfig:
        provider = "local"
        model = "fake"
        create_client = lambda self: fake  # noqa: E731

    # El runner hace `from ..llm import llm_role_config` al importar (copia la
    # referencia), así que hay que parchear el nombre en el namespace del runner.
    monkeypatch.setattr(runner, "llm_role_config", lambda data, role: _FakeRoleConfig())
    import satellite_py.llm as llm_mod
    monkeypatch.setattr(llm_mod, "llm_role_config", lambda data, role: _FakeRoleConfig())

    # dispatch fake (evita el binario C++)
    import satellite_py.orchestrator as orch

    def fake_dispatch(registry, security, request, agent_host_bin=None, **kwargs):
        return {"status": "SUCCESS", "output": {"result": 5}}

    monkeypatch.setattr(orch, "dispatch", fake_dispatch)

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        resp = tc.post("/api/run", json={"goal": "sumar 2 y 3", "max_rounds": 1})
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]

        # Polling hasta terminar
        deadline = time.time() + 15
        status = ""
        events = []
        while time.time() < deadline:
            info = tc.get(f"/api/run/{run_id}").json()
            events = info["events"]
            status = info["status"]
            if status in ("completed", "failed", "error"):
                break
            time.sleep(0.1)
        assert status == "completed", events
        types = [e["type"] for e in events]
        assert "preprocess" in types
        assert "done" in types
        assert info["result"]["ok"] is True
        # El dispatch se observó con la firma correcta (bug fix del wrapper).
        assert "step_started" in types and "step_result" in types
        step_ok = [e for e in events if e["type"] == "step_result"]
        assert step_ok and step_ok[0]["status"] == "SUCCESS"
        assert step_ok[0]["agent_id"] == 1
