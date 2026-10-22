"""API del escritorio: persistencia del switch Think y validación de capacidades."""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.events.bus import EventBus  # noqa: E402
from satellite.orchestration.sessions import SessionStore  # noqa: E402
from satellite.tui.session import Session  # noqa: E402

TOKEN = "token-think"


@pytest.fixture(autouse=True)
def isolate_global_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "global"))


class RecordingStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def append_event(self, task_id: str, event) -> None:
        self.events.append((task_id, event))


class FakeSession:
    def __init__(self, root, home=None) -> None:
        self._real = Session(root, home=home)
        self.root = Path(root).resolve()
        self.store = SessionStore(self.root)
        self.bus = EventBus()
        self.persistence = RecordingStore()

    def runtime(self, *args, **kwargs):
        return (None, self.bus, self.persistence, None)

    def __getattr__(self, name):
        return getattr(self._real, name)


def make_client(tmp_path: Path) -> TestClient:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    backend = DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=home,
        session_factory=FakeSession,
        recents_path=tmp_path / "recents.json",
    )
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return client


def _saved(tmp_path: Path) -> dict:
    from satellite.config import providers as pconfig

    return pconfig.runtime_config(tmp_path)


def test_save_provider_persists_think_per_provider_and_model(tmp_path) -> None:
    client = make_client(tmp_path)
    response = client.post(
        "/api/providers",
        json={
            "name": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com/v1",
            "reasoning_effort": "high",
            "think": True,
        },
    )
    assert response.status_code == 200, response.text
    entry = _saved(tmp_path)["providers"][0]
    assert entry["think"] is True
    assert entry["reasoning_effort"] == "high"

    provider_id = entry["id"]
    patched = client.patch(f"/api/providers/{provider_id}", json={"think": False})
    assert patched.status_code == 200, patched.text
    assert _saved(tmp_path)["providers"][0]["think"] is False


def test_think_is_rejected_for_a_model_without_a_channel(tmp_path) -> None:
    client = make_client(tmp_path)
    response = client.post(
        "/api/providers",
        json={
            "name": "openai",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "think": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_think"
    assert not (tmp_path / ".satellite/config/config.json").exists()


def test_quick_edit_from_chat_updates_active_provider(tmp_path) -> None:
    """El popover de la píldora usa PATCH /providers/{id} y recibe las capacidades al día."""
    client = make_client(tmp_path)
    created = client.post(
        "/api/providers",
        json={
            "name": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com/v1",
        },
    )
    assert created.status_code == 200, created.text
    provider_id = _saved(tmp_path)["providers"][0]["id"]

    patched = client.patch(
        f"/api/providers/{provider_id}", json={"think": True, "reasoning_effort": "high"}
    )
    assert patched.status_code == 200, patched.text
    vista = patched.json()
    activo = next(p for p in vista["providers"] if p["id"] == vista["active_provider"])
    assert activo["think"] is True
    assert activo["reasoning_effort"] == "high"
    # Capacidades que consume el popover (mismo payload que Ajustes): no hay allowlist nueva.
    assert "off" in activo["effort_options"] and "high" in activo["effort_options"]
    assert activo["effort_meta"]["think_param"] == "thinking"

    # Apagar Think persiste sin tocar el esfuerzo elegido.
    off = client.patch(f"/api/providers/{provider_id}", json={"think": False}).json()
    activo = next(p for p in off["providers"] if p["id"] == off["active_provider"])
    assert activo["think"] is False
    assert activo["reasoning_effort"] == "high"


def test_apply_role_persists_think(tmp_path) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/providers",
        json={
            "name": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com/v1",
        },
    )
    assert created.status_code == 200, created.text
    provider_id = _saved(tmp_path)["providers"][0]["id"]

    applied = client.put(
        "/api/roles",
        json={
            "role": "orchestrator",
            "provider": provider_id,
            "think": False,
            "reasoning_effort": "high",
        },
    )
    assert applied.status_code == 200, applied.text
    configured = applied.json()["configured"]["orchestrator"]
    assert configured["think"] is False
    assert configured["reasoning_effort"] == "high"

    role = _saved(tmp_path)["llm"]["orchestrator"]
    assert role["think"] is False
    assert role["reasoning_effort"] == "high"


def test_switch_model_from_popover_persists_and_keeps_key(tmp_path) -> None:
    """Cambiar de modelo desde la píldora: mismo PATCH, conserva clave y capacidades."""
    client = make_client(tmp_path)
    created = client.post(
        "/api/providers",
        json={
            "name": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-no-se-borra",
        },
    )
    assert created.status_code == 200, created.text
    before = _saved(tmp_path)
    provider_id = before["providers"][0]["id"]
    assert before["llm"]["provider_id"] == provider_id

    patched = client.patch(
        f"/api/providers/{provider_id}",
        json={
            "model": "deepseek-v4-pro",
            "reasoning_effort": "max",
            "think": True,
            "effort_levels": ["low", "high", "max"],
            "effort_control": "reasoning_effort",
            "effort_can_disable": True,
        },
    )
    assert patched.status_code == 200, patched.text
    vista = patched.json()
    activo = next(p for p in vista["providers"] if p["id"] == vista["active_provider"])
    assert activo["model"] == "deepseek-v4-pro"
    assert activo["think"] is True
    assert activo["reasoning_effort"] == "max"

    data = _saved(tmp_path)
    entry = data["providers"][0]
    assert entry["model"] == "deepseek-v4-pro"
    assert entry["api_key"] == "sk-no-se-borra"  # la clave no se pierde
    assert data["llm"]["model"] == "deepseek-v4-pro"  # el runtime usará el nuevo modelo
    assert data["llm"]["provider_id"] == provider_id
    # Capacidades del modelo elegido persistidas junto al proveedor (no allowlist nueva).
    assert entry["models"]["deepseek-v4-pro"]["effort_levels"] == ["low", "high", "max"]


def test_switch_model_without_think_support_is_rejected(tmp_path) -> None:
    client = make_client(tmp_path)
    client.post(
        "/api/providers",
        json={
            "name": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com/v1",
        },
    )
    provider_id = _saved(tmp_path)["providers"][0]["id"]

    response = client.patch(
        f"/api/providers/{provider_id}", json={"model": "gpt-4o-mini", "think": True}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_think"
    # El modelo guardado no cambia si la validación falla.
    assert _saved(tmp_path)["providers"][0]["model"] == "deepseek-flash"


def test_models_endpoint_reports_unknown_provider(tmp_path) -> None:
    client = make_client(tmp_path)
    response = client.get("/api/models?provider=no-existe")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "provider_not_found"
