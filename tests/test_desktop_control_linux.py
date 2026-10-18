"""Backend Linux AT-SPI (F6) con adaptador falso — determinista y sin Linux."""

import asyncio
import sys

import pytest

from satellite.desktop_control import (
    COORDINATES_DISABLED,
    SENSITIVE_TARGET,
    SNAPSHOT_OBSOLETE,
    Action,
    LinuxAtspiBackend,
    backend_for_platform,
)
from satellite.desktop_control import linux as atspi
from satellite.desktop_control.errors import (
    EnvironmentUnavailable,
    MissingDependency,
    UnsupportedPlatform,
)


class FakeAtspiAdapter:
    def __init__(self, windows=None, elements=None) -> None:
        self.windows = (
            windows
            if windows is not None
            else [
                {"id": "atspi-0-0", "title": "Editor", "app": "Demo", "active": True},
                {"id": "atspi-1-0", "title": "Preferencias", "app": "Demo", "active": False},
            ]
        )
        self.elements = (
            elements
            if elements is not None
            else {
                "atspi-0-0": [
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
                "atspi-1-0": [],
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
    adapter = FakeAtspiAdapter()
    backend = LinuxAtspiBackend(adapter=adapter)
    snapshot = run(backend.observe("atspi-0-0"))
    return backend, adapter, snapshot


def test_capabilities_are_partial_and_native() -> None:
    caps = LinuxAtspiBackend(adapter=FakeAtspiAdapter()).capabilities
    assert caps.platform == "linux"
    assert caps.can_observe is True
    assert caps.can_act is True
    assert caps.actions == ("click", "focus", "set_value")
    assert caps.coordinate_fallback is False


def test_list_windows_and_snapshot() -> None:
    backend = LinuxAtspiBackend(adapter=FakeAtspiAdapter())
    windows = run(backend.list_windows())
    assert [w.id for w in windows] == ["atspi-0-0", "atspi-1-0"]
    assert windows[0].active is True
    snapshot = run(backend.observe("atspi-0-0"))
    assert snapshot.quality == "good"
    assert '[e2] Button "Guardar"' in snapshot.text
    assert "secreto" not in snapshot.text


def test_act_click_and_set_value_then_reobserve() -> None:
    backend, adapter, snapshot = _observed()
    click = run(backend.act(snapshot.snapshot_id, Action(kind="click", target="e2")))
    assert click.ok is True
    assert adapter.acts[0]["key"] == "b"

    backend2, _adapter2, snapshot2 = _observed()
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
    run(backend.observe("atspi-0-0"))
    obsolete = run(backend.act(snapshot.snapshot_id, Action(kind="click", target="e2")))
    assert obsolete.code == SNAPSHOT_OBSOLETE


def test_unsupported_native_action_propagates() -> None:
    backend, adapter, snapshot = _observed()
    adapter.act_outcome = {"ok": False, "code": "unsupported_action", "error": "sin Action"}
    result = run(
        backend.act(snapshot.snapshot_id, Action(kind="type", target="e3", text="x"))
    )
    assert result.ok is False
    assert result.code == "unsupported_action"


def test_missing_dependency_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "gi", None)
    with pytest.raises(MissingDependency) as excinfo:
        atspi._import_atspi()
    assert "desktop-control-linux" in str(excinfo.value)
    assert atspi.atspi_available() is False


def test_load_rejects_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(UnsupportedPlatform):
        atspi._load_atspi()


def test_environment_unavailable_without_bus() -> None:
    class FakeAtspi:
        class StateType:
            pass

        @staticmethod
        def init():
            return 0

        @staticmethod
        def get_desktop(index):
            return None

    with pytest.raises(EnvironmentUnavailable):
        atspi._AtspiAdapter(FakeAtspi())


def test_factory_selects_linux() -> None:
    assert isinstance(backend_for_platform("linux"), LinuxAtspiBackend)
