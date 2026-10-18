"""Backend Windows UIA (F2) con la capa pywinauto falsa — determinista, sin SO.

No importa pywinauto ni necesita Windows: se inyecta un ``UiaAdapter`` falso.
"""

import asyncio
import sys

import pytest

from satellite.desktop_control import (
    ALLOWED_ACTIONS,
    COORDINATES_DISABLED,
    SENSITIVE_TARGET,
    SNAPSHOT_OBSOLETE,
    TARGET_MISSING,
    TARGET_NOT_INTERACTABLE,
    Action,
    ObserveLimits,
    UiaAdapter,
    WindowsUiaBackend,
    WindowInfo,
    backend_for_platform,
    pywinauto_available,
)
from satellite.desktop_control import windows as win
from satellite.desktop_control.errors import MissingDependency, UnsupportedPlatform


class FakeUiaAdapter:
    """Adaptador falso: devuelve diccionarios fijos en el contrato de F2."""

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
                    {"key": "a", "role": "Button", "name": "Guardar", "interactable": True},
                    {
                        "key": "b",
                        "role": "Edit",
                        "name": "Nombre",
                        "value": "Ana",
                        "interactable": True,
                    },
                    {
                        "key": "c",
                        "role": "Edit",
                        "name": "Contraseña",
                        "value": "secreto",
                        "sensitive": True,
                        "interactable": True,
                    },
                    {
                        "key": "d",
                        "role": "Button",
                        "name": "Deshabilitado",
                        "enabled": False,
                        "interactable": False,
                    },
                    {
                        "key": "e",
                        "role": "Text",
                        "name": "etiqueta",
                        "interactable": False,
                    },
                ],
                "200": [],
            }
        )
        self.calls: list[tuple] = []
        # Actuación (F3): registra llamadas y permite forzar un resultado.
        self.acts: list[dict] = []
        self.act_outcome: dict | None = None

    def list_windows(self, *, max_windows: int) -> list[dict]:
        self.calls.append(("list_windows", max_windows))
        return list(self.windows[:max_windows])

    def window_elements(self, window_id: str, *, max_elements: int) -> list[dict]:
        self.calls.append(("window_elements", window_id, max_elements))
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


def test_module_does_not_import_pywinauto_at_import_time() -> None:
    assert not hasattr(win, "pywinauto")


def test_fake_adapter_satisfies_protocol() -> None:
    assert isinstance(FakeUiaAdapter(), UiaAdapter)


def test_capabilities_can_act() -> None:
    caps = WindowsUiaBackend(adapter=FakeUiaAdapter()).capabilities
    assert caps.platform == "win32"
    assert caps.can_observe is True
    assert caps.can_act is True
    assert caps.actions == tuple(ALLOWED_ACTIONS)
    assert caps.coordinate_fallback is False


def test_list_windows_maps_contract() -> None:
    backend = WindowsUiaBackend(adapter=FakeUiaAdapter())
    windows = run(backend.list_windows())
    assert [w.id for w in windows] == ["100", "200"]
    assert windows[0] == WindowInfo(id="100", title="Editor", app="Demo", active=True)


def test_observe_builds_compact_snapshot() -> None:
    backend = WindowsUiaBackend(adapter=FakeUiaAdapter())
    snap = run(backend.observe("100"))
    assert snap.quality == "good"
    assert '[e1] Button "Guardar"' in snap.text
    assert '[e2] Edit "Nombre" = "Ana"' in snap.text
    # No operables (deshabilitado, texto) no aparecen en el texto compacto.
    assert "Deshabilitado" not in snap.text
    assert "etiqueta" not in snap.text


def test_observe_never_exposes_sensitive_value() -> None:
    backend = WindowsUiaBackend(adapter=FakeUiaAdapter())
    snap = run(backend.observe("100"))
    assert "secreto" not in snap.text
    sensitive = [e for e in snap.elements if e.sensitive]
    assert sensitive
    assert all(e.value == "" for e in sensitive)


def test_observe_respects_element_limit_and_marks_truncated() -> None:
    many = {
        "100": [
            {"key": f"k{i}", "role": "Button", "name": f"B{i}", "interactable": True}
            for i in range(10)
        ]
    }
    adapter = FakeUiaAdapter(elements=many)
    backend = WindowsUiaBackend(adapter=adapter)
    snap = run(backend.observe("100", ObserveLimits(max_elements=3)))
    assert snap.truncated is True
    assert "elementos no mostrados" in snap.text
    # Nunca se pidió el árbol entero: tope + 1.
    assert ("window_elements", "100", 4) in adapter.calls


def test_observe_poor_quality_without_operable_elements() -> None:
    backend = WindowsUiaBackend(adapter=FakeUiaAdapter())
    snap = run(backend.observe("200"))
    assert snap.quality == "poor"
    assert snap.reason


def test_observe_by_window_info_and_unknown_id() -> None:
    adapter = FakeUiaAdapter()
    backend = WindowsUiaBackend(adapter=adapter)
    info = run(backend.list_windows())[0]
    assert run(backend.observe(info)).window == info
    unknown = run(backend.observe("999"))
    assert unknown.window.id == "999"
    assert unknown.quality == "poor"


def test_refs_are_stable_across_observations() -> None:
    adapter = FakeUiaAdapter()
    backend = WindowsUiaBackend(adapter=adapter)
    first = run(backend.observe("100"))
    second = run(backend.observe("100"))
    assert [e.ref for e in first.elements] == [e.ref for e in second.elements]


def _observed():
    adapter = FakeUiaAdapter()
    backend = WindowsUiaBackend(adapter=adapter)
    snap = run(backend.observe("100"))
    return backend, adapter, snap


def test_act_click_ok_and_reobserves() -> None:
    backend, adapter, snap = _observed()
    result = run(backend.act(snap.snapshot_id, Action(kind="click", target="e1")))
    assert result.ok is True
    assert result.changed is False  # un click no muta nada en el fake
    assert result.after_snapshot_id and result.after_snapshot_id != snap.snapshot_id
    assert len(adapter.acts) == 1
    assert adapter.acts[0]["key"] == "a"


def test_act_set_value_reports_changed_true() -> None:
    backend, adapter, snap = _observed()
    action = Action(kind="set_value", target="e2", text="Bea")
    result = run(backend.act(snap.snapshot_id, action))
    assert result.ok is True and result.changed is True
    assert len(adapter.acts) == 1


def test_act_obsolete_snapshot_does_not_execute() -> None:
    backend, adapter, first = _observed()
    run(backend.observe("100"))  # refresca: invalida el snapshot anterior
    result = run(backend.act(first.snapshot_id, Action(kind="click", target="e1")))
    assert result.ok is False and result.code == SNAPSHOT_OBSOLETE
    assert adapter.acts == []


def test_act_unknown_snapshot_does_not_execute() -> None:
    backend, adapter, _ = _observed()
    result = run(backend.act("no-existe", Action(kind="click", target="e1")))
    assert result.ok is False and result.code == SNAPSHOT_OBSOLETE
    assert adapter.acts == []


def test_act_missing_target_does_not_execute() -> None:
    backend, adapter, snap = _observed()
    result = run(backend.act(snap.snapshot_id, Action(kind="click", target="e99")))
    assert result.ok is False and result.code == TARGET_MISSING
    assert adapter.acts == []


def test_act_not_interactable_does_not_execute() -> None:
    backend, adapter, snap = _observed()
    result = run(backend.act(snap.snapshot_id, Action(kind="click", target="e4")))
    assert result.ok is False and result.code == TARGET_NOT_INTERACTABLE
    assert adapter.acts == []


def test_act_sensitive_write_does_not_execute() -> None:
    backend, adapter, snap = _observed()
    result = run(backend.act(snap.snapshot_id, Action(kind="set_value", target="e3", text="x")))
    assert result.ok is False and result.code == SENSITIVE_TARGET
    assert adapter.acts == []


def test_act_coordinates_disabled_does_not_execute() -> None:
    backend, adapter, snap = _observed()
    result = run(
        backend.act(snap.snapshot_id, Action(kind="click", target="e1", coordinates=(3, 4)))
    )
    assert result.ok is False and result.code == COORDINATES_DISABLED
    assert adapter.acts == []


def test_act_adapter_error_propagates() -> None:
    backend, adapter, snap = _observed()
    adapter.act_outcome = {"ok": False, "code": "unsupported_action", "error": "sin patrón nativo"}
    result = run(backend.act(snap.snapshot_id, Action(kind="click", target="e1")))
    assert result.ok is False
    assert result.code == "unsupported_action"
    assert result.error == "sin patrón nativo"


def test_factory_selects_windows_backend() -> None:
    backend = backend_for_platform("win32")
    assert isinstance(backend, WindowsUiaBackend)
    assert backend.capabilities.can_act is True


def test_factory_selects_platform_backends() -> None:
    from satellite.desktop_control import LinuxAtspiBackend, MacosAxBackend

    assert isinstance(backend_for_platform("darwin"), MacosAxBackend)
    assert isinstance(backend_for_platform("linux"), LinuxAtspiBackend)
    assert isinstance(backend_for_platform("linux2"), LinuxAtspiBackend)
    with pytest.raises(UnsupportedPlatform):
        backend_for_platform("freebsd")


def test_import_error_raises_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pywinauto", None)
    with pytest.raises(MissingDependency) as excinfo:
        win._import_pywinauto()
    assert "desktop-control" in str(excinfo.value)
    assert excinfo.value.extra == "desktop-control"


def test_pywinauto_available_is_false_without_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pywinauto", None)
    assert pywinauto_available() is False


def test_load_pywinauto_rejects_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(UnsupportedPlatform):
        win._load_pywinauto()
