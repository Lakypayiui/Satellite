"""Ajustes de control de escritorio: estado, persistencia, visión y gates."""

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.config import providers as provider_config  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.desktop_control import (  # noqa: E402
    ConfiguredVisionFallback,
    DesktopControlPolicy,
    DesktopControlSettings,
    FakeBackend,
    ObservationHistory,
    load_desktop_settings,
    save_vision_selection,
    vision_fallback_for,
)
from satellite.events.bus import EventBus  # noqa: E402
from satellite.orchestration.sessions import SessionStore  # noqa: E402
from satellite.tools.base import ToolContext  # noqa: E402
from satellite.tools.desktop_control import DesktopObserveTool  # noqa: E402
from satellite.tui.session import Session  # noqa: E402

TOKEN = "token-settings"
DEMO_APP = "demo app"


class RecordingStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def append_event(self, task_id: str, event) -> None:
        self.events.append((task_id, event))


class FakeSession:
    """Sesión real para config/estado, con runtime de mentira (sin proveedor)."""

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


def run(coro):
    return asyncio.run(coro)


def make_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DesktopBackend:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    return DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=home,
        session_factory=FakeSession,
        recents_path=tmp_path / "recents.json",
    )


def make_client(backend: DesktopBackend) -> TestClient:
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {backend.token}"})
    return client


def seed_provider(
    tmp_path: Path, *, provider_id: str = "p1", model: str = "vision-model"
) -> None:
    provider_config.save_config(
        tmp_path,
        {
            "providers": [
                {
                    "id": provider_id,
                    "name": "Vision",
                    "model": model,
                    "base_url": "https://example.invalid",
                }
            ]
        },
    )


# --- estado inicial / activar / desactivar ---------------------------------


def test_initial_state_is_off_and_unconfigured(tmp_path, monkeypatch) -> None:
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)
    data = client.get("/api/desktop-control").json()

    assert data["enabled"] is False
    assert data["vision"]["configured"] is False
    assert data["vision"]["implemented"] is False
    assert "sin proveedor" in data["vision"]["reason"]
    assert data["ocr"]["available"] is False
    assert data["ocr"]["boxes"] is True
    assert data["guards"]["mcp"] is False
    # Leer no escribe la config local del host.
    assert not (tmp_path / "home" / "desktop_control.json").exists()


def test_enable_and_disable_persist_locally(tmp_path, monkeypatch) -> None:
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)

    enabled = client.patch("/api/desktop-control", json={"enabled": True}).json()
    assert enabled["enabled"] is True
    host_file = tmp_path / "home" / "desktop_control.json"
    assert json.loads(host_file.read_text(encoding="utf-8"))["enabled"] is True
    assert load_desktop_settings(host_file).enabled is True

    disabled = client.patch("/api/desktop-control", json={"enabled": False}).json()
    assert disabled["enabled"] is False
    assert load_desktop_settings(host_file).enabled is False


def test_enable_preserves_the_rest_of_the_host_policy(tmp_path, monkeypatch) -> None:
    backend = make_backend(tmp_path, monkeypatch)
    host_file = tmp_path / "home" / "desktop_control.json"
    host_file.write_text(
        json.dumps(
            {
                "enabled": False,
                "allowed_processes": ["demo app"],
                "denied_apps": ["terminal"],
                "max_actions_per_run": 3,
                "max_seconds_per_run": 5.0,
                "rate_per_minute": 2,
                "confirm_destructive": True,
            }
        ),
        encoding="utf-8",
    )
    client = make_client(backend)
    client.patch("/api/desktop-control", json={"enabled": True})

    saved = json.loads(host_file.read_text(encoding="utf-8"))
    assert saved["enabled"] is True
    assert saved["allowed_processes"] == ["demo app"]
    # La denylist por defecto es un suelo: se conserva "terminal" y se suman las
    # apps protegidas aunque el archivo sea antiguo.
    assert "terminal" in saved["denied_apps"]
    assert "keepass" in saved["denied_apps"]
    assert saved["max_actions_per_run"] == 3
    assert saved["max_seconds_per_run"] == 5.0
    assert saved["rate_per_minute"] == 2


def test_allowlist_can_be_edited_and_persists(tmp_path, monkeypatch) -> None:
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)

    data = client.patch(
        "/api/desktop-control",
        json={"enabled": True, "allowed_processes": ["Safari", "Demo App", "demo app"]},
    ).json()

    # Se normaliza y se deduplica; la vista devuelve la allowlist efectiva.
    assert data["allowed_processes"] == ["safari", "demo app"]
    assert data["backend"]["platform"]
    host_file = tmp_path / "home" / "desktop_control.json"
    saved = json.loads(host_file.read_text(encoding="utf-8"))
    assert saved["allowed_processes"] == ["safari", "demo app"]
    assert saved["enabled"] is True


def test_allowlist_rejects_a_denied_app(tmp_path, monkeypatch) -> None:
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)

    response = client.patch(
        "/api/desktop-control", json={"allowed_processes": ["Terminal"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "app_denied"
    # Nada se escribe si la lista es inválida.
    assert not (tmp_path / "home" / "desktop_control.json").exists()


# --- selección de proveedor/modelo ------------------------------------------


def test_provider_and_model_persist_in_existing_config(tmp_path, monkeypatch) -> None:
    seed_provider(tmp_path)
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)

    data = client.patch(
        "/api/desktop-control",
        json={
            "enabled": True,
            "vision_provider": "p1",
            "vision_model": "vision-model-2",
        },
    ).json()

    assert data["enabled"] is True
    assert data["vision"]["configured"] is True
    assert data["vision"]["provider"] == "p1"
    assert data["vision"]["model"] == "vision-model-2"
    assert data["vision"]["available"] is True
    # La visión real sigue sin implementarse.
    assert data["vision"]["implemented"] is False

    config = provider_config.load_config(tmp_path)
    assert config["desktop_control"] == {
        "vision_provider": "p1",
        "vision_model": "vision-model-2",
    }
    # El resto de la config se conserva.
    assert config["providers"][0]["id"] == "p1"


def test_provider_without_model_uses_its_saved_model(tmp_path, monkeypatch) -> None:
    seed_provider(tmp_path, model="vision-default")
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)

    data = client.patch(
        "/api/desktop-control", json={"vision_provider": "p1"}
    ).json()
    assert data["vision"]["model"] == "vision-default"
    assert data["vision"]["configured"] is True


def test_unknown_provider_is_rejected_and_nothing_persists(
    tmp_path, monkeypatch
) -> None:
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)

    response = client.patch(
        "/api/desktop-control", json={"vision_provider": "no-existe"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "provider_not_found"
    assert provider_config.load_config(tmp_path).get("desktop_control") is None


def test_selected_provider_removed_reports_unavailable(tmp_path, monkeypatch) -> None:
    seed_provider(tmp_path)
    backend = make_backend(tmp_path, monkeypatch)
    client = make_client(backend)
    client.patch("/api/desktop-control", json={"vision_provider": "p1"})

    # Quitar el proveedor conservando la selección: debe reportarse como no disponible.
    data = provider_config.load_config(tmp_path)
    data["providers"] = []
    provider_config.save_config(tmp_path, data)
    view = client.get("/api/desktop-control").json()
    assert view["vision"]["configured"] is False
    assert view["vision"]["implemented"] is False
    assert "ya no existe" in view["vision"]["reason"]


def test_vision_fallback_resolution(tmp_path) -> None:
    seed_provider(tmp_path, provider_id="p1", model="vision-model")
    save_vision_selection(tmp_path, provider="p1", model="vision-model-2")
    fallback = vision_fallback_for(tmp_path)
    assert fallback.configured is True
    assert fallback.provider == "p1"
    assert fallback.model == "vision-model-2"
    assert fallback.implemented is False


# --- gates conservados y MCP ------------------------------------------------


def test_vision_selection_does_not_bypass_f4_gates(tmp_path) -> None:
    settings = DesktopControlSettings(
        enabled=True, allowed_processes=(DEMO_APP,), denied_apps=()
    )
    policy = DesktopControlPolicy(settings)
    vision = ConfiguredVisionFallback(provider="p1", model="vision-model")
    tool = DesktopObserveTool(
        backend=FakeBackend(), policy=policy, history=ObservationHistory(), vision=vision
    )
    ctx = ToolContext(project_root=tmp_path, task_id="t1", session_id="s1", approval=None)

    denied = run(tool.execute({"window": "win-main"}, ctx))
    assert denied.success is False
    assert denied.error_code == "approval_required"
    assert policy.grants.entries() == []
    assert policy.settings == settings


def test_observe_reports_selected_vision_without_pretending_vision_works(
    tmp_path,
) -> None:
    from satellite.approval.base import ApprovalPort
    from satellite.core.proposal import ApprovalDecision, ApprovalStatus

    class HumanPort(ApprovalPort):
        async def request(self, proposal, ctx):
            return ApprovalDecision(status=ApprovalStatus.APPROVED, reason="test")

    settings = DesktopControlSettings(
        enabled=True, allowed_processes=(DEMO_APP,), denied_apps=()
    )
    vision = ConfiguredVisionFallback(provider="p1", model="vision-model")
    tool = DesktopObserveTool(
        backend=FakeBackend(),
        policy=DesktopControlPolicy(settings),
        history=ObservationHistory(),
        vision=vision,
    )
    ctx = ToolContext(
        project_root=tmp_path,
        task_id="t1",
        session_id="s1",
        approval=HumanPort(),
    )
    result = run(tool.execute({"window": "win-main"}, ctx))
    assert result.success is True
    assert result.output["vision"]["configured"] is True
    assert result.output["vision"]["provider"] == "p1"
    assert result.output["vision"]["model"] == "vision-model"
    assert result.output["vision"]["implemented"] is False
    assert list(result.output.keys())[-1] == "data"


def test_mcp_does_not_expose_desktop_control() -> None:
    from satellite.cli.runtime_builder import build_tool_registry
    from satellite.mcp.handlers import TOOLS_LIST
    from satellite.tool_manifest import TOOL_IDS

    names = {str(item.get("name")) for item in TOOLS_LIST}
    assert not (
        {"desktop_control", "desktop_observe", "desktop_act", "desktop_list_windows"}
        & names
    )
    assert {tool.id for tool in build_tool_registry().all()}.isdisjoint(TOOL_IDS)
