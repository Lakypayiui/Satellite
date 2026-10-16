"""API del backend de escritorio: auth, proyectos, sesiones, tareas, cambios y trabajos."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.orchestration.chat import ChatReply  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.events.bus import EventBus  # noqa: E402
from satellite.orchestration.sessions import SessionStore  # noqa: E402
from satellite.persistence.changes import ChangeLedger  # noqa: E402
from satellite.tools.jobs import registry_for  # noqa: E402
from satellite.tui.session import Session  # noqa: E402

TOKEN = "token-local-de-prueba"


class RecordingStore:
    """Almacén mínimo: guarda lo que el backend persiste."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def append_event(self, task_id: str, event) -> None:
        self.events.append((task_id, event))


class FakeSession:
    """``Session`` de verdad para lo que no necesita proveedor, con runtime de mentira."""

    def __init__(self, root, home=None) -> None:
        self._real = Session(root, home=home)
        self.root = Path(root).resolve()
        self.store = SessionStore(self.root)
        self.bus = EventBus()
        self.persistence = RecordingStore()

    def runtime(self):
        return (None, self.bus, self.persistence, None)

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def send_message(self, text, *, approval=None, plan_mode=False, extra_metadata=None):
        return ChatReply(text=f"eco: {text}", task_id="t-fake", status="ok")

    def new_session(self, name: str = "") -> str:
        return self.store.create(name)

    def use_session(self, target: str) -> str | None:
        return self.store.switch(target)


class BrokenSession(FakeSession):
    """Un proyecto sin proveedor: el runtime no se puede armar y hay que decirlo."""

    def runtime(self):
        raise RuntimeError("falta configurar un proveedor (satellite provider)")


def make_backend(tmp_path: Path, factory=FakeSession, *, token: str = TOKEN) -> DesktopBackend:
    return DesktopBackend(
        tmp_path,
        token=token,
        home=tmp_path / "home",
        session_factory=factory,
        recents_path=tmp_path / "recents.json",
    )


def make_client(backend: DesktopBackend) -> TestClient:
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {backend.token}"})
    return client


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def entorno(tmp_path: Path):
    monkeypatch_home = tmp_path / "home"
    monkeypatch_home.mkdir(parents=True, exist_ok=True)
    backend = make_backend(tmp_path)
    return make_client(backend), backend


# --- autenticación ---


def test_the_api_requires_the_local_token(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    client = TestClient(create_app(backend))

    sin_token = client.get("/api/status")
    malo = client.get("/api/status", headers={"Authorization": "Bearer otro-token"})
    esquema_raro = client.get("/api/status", headers={"Authorization": TOKEN})
    bueno = client.get("/api/status", headers={"Authorization": f"Bearer {backend.token}"})

    assert sin_token.status_code == 401
    assert sin_token.json()["detail"]["error"]["code"] == "unauthorized"
    assert malo.status_code == 401
    assert esquema_raro.status_code == 401  # sin el esquema no vale
    assert bueno.status_code == 200


def test_health_is_the_only_open_door_and_says_nothing(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    client = TestClient(create_app(backend))

    respuesta = client.get("/api/health")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True}


def test_no_response_ever_carries_the_token_or_the_provider_key(tmp_path: Path) -> None:
    backend = make_backend(tmp_path, token="token-secreto-123")
    client = make_client(backend)
    config = tmp_path / ".satellite" / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.json").write_text(
        json.dumps({"name": "openai", "model": "m", "api_key": "sk-no-debe-salir"}),
        encoding="utf-8",
    )

    for ruta in (
        "/api/status",
        "/api/config",
        "/api/providers",
        "/api/sessions",
        "/api/projects",
    ):
        cuerpo = client.get(ruta).text
        assert "token-secreto-123" not in cuerpo
        assert "sk-no-debe-salir" not in cuerpo


# --- proyectos ---


def test_projects_are_remembered_and_validated(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    client = make_client(backend)
    otro = tmp_path / "otro"
    otro.mkdir()

    inicial = client.get("/api/projects").json()
    abierto = client.post("/api/projects", json={"path": str(otro)})
    despues = client.get("/api/projects").json()
    inexistente = client.post("/api/projects", json={"path": str(tmp_path / "no-existe")})

    assert inicial["current"] == str(tmp_path)
    assert abierto.status_code == 200
    assert abierto.json()["project_root"] == str(otro)
    assert despues["current"] == str(otro)
    assert despues["recents"][0] == str(otro)
    assert inexistente.status_code == 404
    assert inexistente.json()["error"]["code"] == "project_not_found"


def test_status_says_whether_the_runtime_is_ready_and_why_not(tmp_path: Path) -> None:
    client = make_client(make_backend(tmp_path))

    estado = client.get("/api/status").json()

    assert "runtime_ready" in estado
    if not estado["runtime_ready"]:
        assert estado["runtime_error"]  # si no está listo, se dice el motivo


# --- sesiones ---


def test_sessions_are_listed_renamed_and_read(tmp_path: Path) -> None:
    client = make_client(make_backend(tmp_path))
    store = SessionStore(tmp_path)
    primera = store.create("primera")
    store.save(primera, [{"role": "user", "content": "hola"}], name="primera")
    segunda = store.create("segunda")
    store.switch(primera)

    listado = client.get("/api/sessions").json()
    renombrada = client.patch(f"/api/sessions/{primera}", json={"name": "renombrada"})
    turnos = client.get(f"/api/sessions/{primera}/turns").json()
    fallo = client.patch("/api/sessions/no-existe", json={"name": "x"})

    assert listado["current"] == primera
    assert {item["id"] for item in listado["sessions"]} >= {primera, segunda}
    assert renombrada.status_code == 200
    assert turnos["turns"][0]["content"] == "hola"
    assert fallo.status_code == 404


def test_three_sessions_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """Lo que pide el enunciado: reanudar sin perder conversaciones previas."""
    client = make_client(make_backend(tmp_path))
    store = SessionStore(tmp_path)
    ids = [store.create(f"sesión {i}") for i in range(3)]
    for index, session_id in enumerate(ids[:2]):
        store.save(
            session_id,
            [{"role": "user", "content": f"soy la {index}"}],
            name=f"sesión {index}",
        )

    assert client.get(f"/api/sessions/{ids[0]}/turns").json()["turns"][0]["content"] == "soy la 0"
    assert client.get(f"/api/sessions/{ids[1]}/turns").json()["turns"][0]["content"] == "soy la 1"
    assert client.get(f"/api/sessions/{ids[2]}/turns").json()["turns"] == []

    activa = client.post(f"/api/sessions/{ids[0]}/activate")

    assert activa.status_code == 200
    assert client.get("/api/sessions").json()["current"] == ids[0]
    # y las otras dos siguen intactas
    assert client.get(f"/api/sessions/{ids[1]}/turns").json()["turns"][0]["content"] == "soy la 1"


def test_creating_a_session_without_a_runtime_explains_why(tmp_path: Path) -> None:
    client = make_client(make_backend(tmp_path, BrokenSession))

    respuesta = client.post("/api/sessions", json={"name": "nueva"})

    assert respuesta.status_code == 503
    assert respuesta.json()["error"]["code"] == "runtime_unavailable"
    assert "proveedor" in respuesta.json()["error"]["message"]


def test_a_message_round_trip_reports_the_reply(tmp_path: Path, entorno) -> None:
    client, backend = entorno

    respuesta = client.post("/api/messages", json={"text": "hola agente"})

    assert respuesta.status_code == 200
    assert respuesta.json()["text"] == "eco: hola agente"
    assert client.get("/api/status").json()["runtime_ready"] is True


def test_a_message_needs_text(tmp_path: Path, entorno) -> None:
    client, _backend = entorno

    vacío = client.post("/api/messages", json={"text": ""})

    assert vacío.status_code == 422


# --- tareas, cambios y trabajos ---


def test_an_unknown_task_is_a_clear_404(tmp_path: Path, entorno) -> None:
    client, _backend = entorno

    respuesta = client.get("/api/tasks/no-existe")

    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["code"] == "task_not_found"


def test_restore_nothing_without_confirmation(tmp_path: Path, entorno) -> None:
    """Lo que pide el enunciado: ver qué se descartaría antes de tocar nada."""
    client, _backend = entorno
    tarea = "t-restore"
    notas = tmp_path / "notas.txt"
    notas.write_text("v2", encoding="utf-8")
    ChangeLedger(tmp_path).record(tarea, notas, "v1")

    previa = client.post(f"/api/tasks/{tarea}/restore", json={})
    sigue = notas.read_text(encoding="utf-8")
    confirmada = client.post(f"/api/tasks/{tarea}/restore", json={"confirm": True})

    assert previa.status_code == 200
    assert previa.json()["confirmed"] is False
    assert "v1" in previa.json()["preview"]["diff"]
    assert sigue == "v2"  # sin confirmación no se descartó nada
    assert confirmada.status_code == 200
    assert confirmada.json()["confirmed"] is True
    assert notas.read_text(encoding="utf-8") == "v1"


def test_jobs_are_listed_with_their_output(tmp_path: Path, entorno) -> None:
    client, _backend = entorno
    registry = registry_for(tmp_path)
    info = registry.start(
        [sys.executable, "-c", "print('hola trabajo')"],
        cwd=tmp_path,
        env=os.environ.copy(),
        command="eco",
    )
    run(registry.wait(info.id, 30))

    listado = client.get("/api/jobs").json()["jobs"]
    salida = client.get(f"/api/jobs/{info.id}/output").json()
    desconocido = client.get("/api/jobs/no-existe/output")

    assert [item["job_id"] for item in listado] == [info.id]
    assert "hola trabajo" in salida["output"]
    assert salida["controllable"] is True
    assert desconocido.status_code == 404


def test_a_foreign_job_cannot_be_stopped_and_it_is_said(tmp_path: Path, entorno) -> None:
    client, _backend = entorno
    ajeno = tmp_path / ".satellite" / "state" / "jobs" / "ajeno1.json"
    ajeno.parent.mkdir(parents=True, exist_ok=True)
    ajeno.write_text(
        json.dumps({"job_id": "ajeno1", "command": "algo", "status": "running"}),
        encoding="utf-8",
    )

    respuesta = client.post("/api/jobs/ajeno1/stop")

    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["code"] == "job_foreign"


# --- configuración ---


def test_policy_mode_can_be_changed_and_is_validated(tmp_path: Path, entorno) -> None:
    client, _backend = entorno

    cambiado = client.put("/api/config", json={"policy_mode": "auto"})
    invalido = client.put("/api/config", json={"policy_mode": "permite-todo"})

    assert cambiado.status_code == 200
    assert cambiado.json()["policy_mode"] == "auto"
    assert invalido.status_code == 422
    assert invalido.json()["error"]["code"] == "invalid_policy"


def test_providers_must_be_complete(tmp_path: Path, entorno) -> None:
    client, _backend = entorno

    en_blanco = client.post(
        "/api/providers", json={"name": "x", "model": "  ", "base_url": "  "}
    )
    mal_formado = client.post("/api/providers", json={"name": "x"})

    assert en_blanco.status_code == 422
    assert en_blanco.json()["error"]["code"] == "invalid_provider"
    assert mal_formado.status_code == 422
    assert mal_formado.json()["error"]["code"] == "invalid_request"
