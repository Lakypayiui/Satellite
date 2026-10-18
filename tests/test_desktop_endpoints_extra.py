"""Endpoints nuevos del escritorio: catálogo, roles, doctor, stats, snapshots y skills."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.events.bus import EventBus  # noqa: E402
from satellite.tui.session import Session  # noqa: E402

TOKEN = "token-extra"


@pytest.fixture(autouse=True)
def isolate_global_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "global"))


class RecordingStore:
    """Almacén mínimo para el puente."""

    async def append_event(self, task_id: str, event) -> None:
        return None


class FakeSession:
    """``Session`` con runtime de mentira: los roles no lo necesitan de verdad."""

    def __init__(self, root, home=None) -> None:
        self.root = Path(root)
        self.bus = EventBus()
        self.persistence = RecordingStore()

    def runtime(self, *, force: bool = False):
        return (None, self.bus, self.persistence, None)


def make_client(tmp_path: Path, *, factory=None) -> TestClient:
    backend = DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        recents_path=tmp_path / "recents.json",
        **({} if factory is None else {"session_factory": factory}),
    )
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return client


def run(coro):
    return asyncio.run(coro)


# --- catálogo ---


def test_the_catalog_brings_the_url_so_it_does_not_have_to_be_typed(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    catalogo = client.get("/api/providers/catalog").json()["catalog"]

    assert catalogo
    primero = catalogo[0]
    assert primero["base_url"].startswith("http")
    assert primero["key"] and primero["label"] and primero["family"]
    familias = {entrada["family"] for entrada in catalogo}
    assert familias <= {"openai", "anthropic"}
    assert "openai" in familias  # todo lo compatible se agrupa ahí


# --- modelos ---


def test_loading_models_from_a_bad_url_says_why(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    respuesta = client.get("/api/models", params={"base_url": "http://127.0.0.1:9/v1"})

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["ok"] is False
    assert datos["error"]  # se dice el motivo, no se inventan modelos


def test_models_needs_a_url(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    respuesta = client.get("/api/models", params={"base_url": "  "})

    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "invalid_request"


def test_model_refresh_resolves_catalog_environment_variable_server_side(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from satellite.config.models import ModelsResult

    backend = DesktopBackend(tmp_path, token=TOKEN, home=tmp_path / "home")
    monkeypatch.setenv("COMMANDCODE_ANTHROPIC_API_KEY", "cmd-test-key")
    captured: dict = {}

    def load_models(base_url, family, api_key, provider, account_id):
        captured.update(
            base_url=base_url,
            family=family,
            api_key=api_key,
            provider=provider,
            account_id=account_id,
        )
        return ModelsResult(ok=True, models=("example/model",))

    monkeypatch.setattr(backend, "session", lambda _project_root=None: SimpleNamespace(load_models=load_models))
    result = backend.models(
        base_url="https://api.commandcode.ai/provider",
        family="anthropic",
        api_key_env="COMMANDCODE_ANTHROPIC_API_KEY",
    )

    assert result["ok"] is True
    assert captured["api_key"] == "cmd-test-key"
    assert captured["provider"] == "commandcode-anthropic"


# --- roles ---


def test_a_role_is_applied_without_touching_the_others(tmp_path: Path) -> None:
    """Lo que se acordó: cada rol con su botón, sin cascada."""
    from satellite.config import providers as pconfig

    client = make_client(tmp_path, factory=FakeSession)
    proveedor = pconfig.save_provider(
        tmp_path, name="openai", model="gpt-4o-mini", base_url="https://api.openai.com/v1"
    ).id

    aplicado = client.put(
        "/api/roles", json={"role": "orchestrator", "provider": proveedor, "model": "gpt-4o"}
    )

    assert aplicado.status_code == 200
    vista = aplicado.json()
    assert vista["configured"]["orchestrator"]["provider"] == proveedor
    assert vista["configured"]["orchestrator"]["model"] == "gpt-4o"
    # y los demás roles no se han tocado
    assert "context" not in vista["configured"]


def test_a_role_can_be_returned_to_the_global_one(tmp_path: Path) -> None:
    from satellite.config import providers as pconfig

    client = make_client(tmp_path, factory=FakeSession)
    proveedor = pconfig.save_provider(
        tmp_path, name="openai", model="m", base_url="https://api.openai.com/v1"
    ).id
    client.put("/api/roles", json={"role": "agents", "provider": proveedor})

    limpiado = client.put("/api/roles", json={"role": "agents", "provider": ""})

    assert limpiado.status_code == 200
    assert "agents" not in limpiado.json()["configured"]


def test_a_role_and_a_provider_are_validated(tmp_path: Path) -> None:
    client = make_client(tmp_path, factory=FakeSession)

    rol_raro = client.put("/api/roles", json={"role": "inventado", "provider": "x"})
    proveedor_raro = client.put("/api/roles", json={"role": "agents", "provider": "no-existe"})

    assert rol_raro.status_code == 422
    assert rol_raro.json()["error"]["code"] == "invalid_role"
    assert proveedor_raro.status_code == 404
    assert proveedor_raro.json()["error"]["code"] == "provider_not_found"


# --- mantenimiento (con la Session real: funciona sin proveedor) ---


def test_doctor_reports_each_check(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    respuesta = client.get("/api/doctor")

    assert respuesta.status_code == 200
    checks = respuesta.json()["checks"]
    assert checks and all("ok" in item and "mensaje" in item for item in checks)


def test_stats_and_snapshots_answer(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    stats = client.get("/api/stats")
    snapshots = client.get("/api/snapshots")

    assert stats.status_code == 200 and "skills" in stats.json() and "cache" in stats.json()
    assert snapshots.status_code == 200 and isinstance(snapshots.json()["snapshots"], list)


def test_restoring_a_snapshot_or_resetting_needs_confirmation(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    restaure = client.post("/api/restore", json={"name": "cualquiera"})
    reseteo = client.post("/api/reset", json={"keep_skills": True})

    assert restaure.status_code == 200
    assert restaure.json()["confirmed"] is False
    assert "no se ha restaurado nada" in restaure.json()["note"]
    assert reseteo.status_code == 200
    assert reseteo.json()["confirmed"] is False


# --- skills y agentes ---


def test_searching_skills_answers_with_a_list(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    respuesta = client.get("/api/skills/search", params={"q": "nada"})

    assert respuesta.status_code == 200
    assert isinstance(respuesta.json()["results"], list)


def test_curating_and_creating_agents_say_when_there_is_no_runtime(tmp_path: Path) -> None:
    """Sin proveedor no hay runtime: se dice, no se simula un resultado."""
    client = make_client(tmp_path)

    curar = client.post("/api/curate", json={"request": "algo"})
    agente = client.post("/api/agents", json={"need": "algo"})

    assert curar.status_code == 503 and curar.json()["error"]["code"] == "runtime_unavailable"
    assert agente.status_code == 503 and agente.json()["error"]["code"] == "runtime_unavailable"
