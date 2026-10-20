"""Backend macOS AX (F6) con adaptador falso — determinista y sin macOS."""

import asyncio
import sys

import pytest

from satellite.desktop_control import (
    COORDINATES_DISABLED,
    SENSITIVE_TARGET,
    SNAPSHOT_OBSOLETE,
    Action,
    MacosAxBackend,
    backend_for_platform,
)
from satellite.desktop_control import macos as ax
from satellite.desktop_control.errors import (
    EnvironmentUnavailable,
    MissingDependency,
    UnsupportedPlatform,
)


class FakeAxAdapter:
    """Adaptador falso con el contrato de `DesktopAdapter`."""

    def __init__(self, windows=None, elements=None) -> None:
        self.windows = (
            windows
            if windows is not None
            else [
                {"id": "100", "title": "Editor", "app": "Demo", "active": True},
                {"id": "200", "title": "Preferencias", "app": "Demo", "active": False},
            ]
        )
        self.elements = (
            elements
            if elements is not None
            else {
                "100": [
                    {"key": "a", "role": "Window", "name": "Editor", "interactable": False},
                    {"key": "b", "role": "Button", "name": "Guardar", "interactable": True},
                    {
                        "key": "c",
                        "role": "Edit",
                        "name": "Nombre",
                        "value": "Ana",
                        "interactable": True,
                    },
                    {
                        "key": "d",
                        "role": "Edit",
                        "name": "Contraseña",
                        "value": "secreto",
                        "sensitive": True,
                        "interactable": True,
                    },
                    {"key": "e", "role": "Text", "name": "etiqueta", "interactable": False},
                ],
                "200": [],
            }
        )
        self.acts: list[dict] = []
        self.act_outcome: dict | None = None

    def list_windows(self, *, max_windows: int) -> list[dict]:
        return list(self.windows[:max_windows])

    def window_elements(self, window_id: str, *, max_elements: int) -> list[dict]:
        return list(self.elements.get(window_id, []))[:max_elements]

    def act(self, window_id: str, node_key: str, action: dict) -> dict:
        self.acts.append({"window_id": window_id, "key": node_key, "action": dict(action)})
        if self.act_outcome is not None:
            return dict(self.act_outcome)
        if str(action.get("action")) == "set_value":
            for node in self.elements.get(window_id, []):
                if str(node.get("key")) == node_key:
                    node["value"] = str(action.get("text", ""))
                    break
        return {"ok": True, "code": "", "error": ""}


def run(coro):
    return asyncio.run(coro)


def _observed():
    adapter = FakeAxAdapter()
    backend = MacosAxBackend(adapter=adapter)
    snapshot = run(backend.observe("100"))
    return backend, adapter, snapshot


def test_capabilities_are_partial_and_native() -> None:
    caps = MacosAxBackend(adapter=FakeAxAdapter()).capabilities
    assert caps.platform == "darwin"
    assert caps.can_observe is True
    assert caps.can_act is True
    assert caps.actions == ("click", "focus", "set_value", "scroll")
    assert caps.coordinate_fallback is False


def test_list_windows_and_snapshot() -> None:
    backend = MacosAxBackend(adapter=FakeAxAdapter())
    windows = run(backend.list_windows())
    assert [w.id for w in windows] == ["100", "200"]
    assert windows[0].active is True
    snapshot = run(backend.observe("100"))
    assert snapshot.quality == "good"
    assert '[e2] Button "Guardar"' in snapshot.text
    assert "secreto" not in snapshot.text


def test_act_click_and_set_value_then_reobserve() -> None:
    backend, adapter, snapshot = _observed()
    click = run(backend.act(snapshot.snapshot_id, Action(kind="click", target="e2")))
    assert click.ok is True
    assert adapter.acts[0]["key"] == "b"

    backend2, adapter2, snapshot2 = _observed()
    edit = run(
        backend2.act(snapshot2.snapshot_id, Action(kind="set_value", target="e3", text="Bea"))
    )
    assert edit.ok is True
    assert edit.changed is True


def test_act_does_not_bypass_f4_security() -> None:
    backend, adapter, snapshot = _observed()
    stale = run(backend.act("no-existe", Action(kind="click", target="e1")))
    assert stale.code == SNAPSHOT_OBSOLETE
    sensitive = run(
        backend.act(snapshot.snapshot_id, Action(kind="set_value", target="e4", text="x"))
    )
    assert sensitive.code == SENSITIVE_TARGET
    coords = run(
        backend.act(
            snapshot.snapshot_id,
            Action(kind="click", target="e2", coordinates=(1, 2)),
        )
    )
    assert coords.code == COORDINATES_DISABLED
    assert adapter.acts == []
    # Un snapshot nuevo invalida el anterior.
    run(backend.observe("100"))
    obsolete = run(backend.act(snapshot.snapshot_id, Action(kind="click", target="e2")))
    assert obsolete.code == SNAPSHOT_OBSOLETE


def test_unsupported_native_action_propagates() -> None:
    backend, adapter, snapshot = _observed()
    adapter.act_outcome = {"ok": False, "code": "unsupported_action", "error": "sin AXPress"}
    result = run(
        backend.act(snapshot.snapshot_id, Action(kind="type", target="e3", text="x"))
    )
    assert result.ok is False
    assert result.code == "unsupported_action"


def test_missing_dependency_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ApplicationServices", None)
    monkeypatch.setitem(sys.modules, "Quartz", None)
    with pytest.raises(MissingDependency) as excinfo:
        ax._import_ax()
    assert "desktop-control-macos" in str(excinfo.value)
    assert ax.ax_available() is False


def test_load_rejects_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(UnsupportedPlatform):
        ax._load_ax()


def test_environment_unavailable_without_accessibility_permission() -> None:
    class FakeAX:
        @staticmethod
        def AXIsProcessTrusted():
            return False

    with pytest.raises(EnvironmentUnavailable):
        ax._AxAdapter(FakeAX(), object())


def test_factory_selects_macos() -> None:
    assert isinstance(backend_for_platform("darwin"), MacosAxBackend)
