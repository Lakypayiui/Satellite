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
        assert ids == {1, 2, 3}
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

    # LLM scripted: 1) preprocess dice suficiente, 2) plan con un paso,
    # 3) compressor devuelve doc neutro.
    fake = _ScriptedClient(
        [
            json.dumps({"category": "general", "sufficient": True, "description": "ok"}),
            json.dumps({
                "goal": "sumar",
                "steps": [
                    {"agent_id": 1, "input": {"a": 2, "b": 3},
                     "dependencies": [], "description": "sumar", "order": 0,
                     "context": {"paths": ["math_utils.py"], "symbols": ["add"]}},
                ],
            }),
            json.dumps({"intention": "sumar", "constraints": [], "references": {}, "status": ""}),
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

    def fake_dispatch(registry, security, request, agent_host_bin=None):
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
