"""Tests de contrato de los modelos de control de escritorio (F1)."""

import asyncio
import inspect
from dataclasses import FrozenInstanceError

import pytest

from satellite.core.errors import ContractError
from satellite.desktop_control import (
    ActResult,
    Action,
    ActionDecision,
    Capabilities,
    DesktopBackend,
    Element,
    ObserveLimits,
    Snapshot,
    WindowInfo,
)


def _window() -> WindowInfo:
    return WindowInfo(id="w1", title="Ventana", app="App", active=True)


def test_window_round_trip() -> None:
    w = _window()
    assert WindowInfo.from_dict(w.to_dict()) == w


def test_element_round_trip_without_bounds() -> None:
    e = Element(ref="e1", role="Button", name="Guardar", actions=("click",))
    d = e.to_dict()
    assert "bounds" not in d
    assert Element.from_dict(d) == e


def test_element_round_trip_with_bounds() -> None:
    e = Element(ref="e1", role="Button", name="Guardar", bounds=(1, 2, 3, 4))
    assert "bounds" in e.to_dict()
    assert Element.from_dict(e.to_dict()) == e


def test_observe_limits_round_trip() -> None:
    limits = ObserveLimits(max_elements=5, max_chars=100, mode="diff")
    assert ObserveLimits.from_dict(limits.to_dict()) == limits


def test_snapshot_round_trip() -> None:
    snap = Snapshot(
        snapshot_id="s1",
        window=_window(),
        elements=(Element(ref="e1", role="Button", name="Ok"),),
        text='ventana: "Ventana"',
        quality="good",
        mode="full",
    )
    assert Snapshot.from_dict(snap.to_dict()) == snap


def test_act_result_round_trip() -> None:
    r = ActResult(ok=True, action="click", ref="e1", changed=True, after_snapshot_id="s2")
    assert ActResult.from_dict(r.to_dict()) == r


def test_capabilities_round_trip() -> None:
    c = Capabilities(platform="fake", can_act=True, actions=("click",), sources=("fixtures",))
    assert Capabilities.from_dict(c.to_dict()) == c


def test_action_round_trip_and_kind_alias() -> None:
    a = Action(kind="click", target="e1", text="x")
    assert a.to_dict()["action"] == "click"
    assert Action.from_dict(a.to_dict()) == a
    assert Action.from_dict({"action": "key", "text": "Tab"}).kind == "key"
    assert Action.from_dict({"kind": "focus", "target": "e2"}).kind == "focus"


def test_action_decision_round_trip() -> None:
    d = ActionDecision(ok=True, element=Element(ref="e1", role="Button"))
    assert ActionDecision.from_dict(d.to_dict()) == d
    assert ActionDecision.from_dict({"ok": False, "code": "x", "reason": "y"}).element is None


def test_empty_ids_rejected() -> None:
    with pytest.raises(ContractError):
        WindowInfo(id="")
    with pytest.raises(ContractError):
        Element(ref="", role="Button")
    with pytest.raises(ContractError):
        Element(ref="e1", role="")
    with pytest.raises(ContractError):
        Snapshot(snapshot_id="", window=_window())
    with pytest.raises(ContractError):
        Action(kind="")


def test_invalid_quality_and_mode_rejected() -> None:
    with pytest.raises(ContractError):
        Snapshot(snapshot_id="s", window=_window(), quality="medio")
    with pytest.raises(ContractError):
        Snapshot(snapshot_id="s", window=_window(), mode="parcial")
    with pytest.raises(ContractError):
        ObserveLimits(mode="parcial")


def test_invalid_limits_rejected() -> None:
    with pytest.raises(ContractError):
        ObserveLimits(max_elements=0)
    with pytest.raises(ContractError):
        ObserveLimits(max_chars=0)


def test_bounds_and_coordinates_validation() -> None:
    with pytest.raises(ContractError):
        Element(ref="e1", role="Button", bounds=(1, 2, 3))
    with pytest.raises(ContractError):
        Action(kind="click", coordinates=(1,))


def test_models_are_frozen() -> None:
    w = _window()
    with pytest.raises(FrozenInstanceError):
        w.id = "otro"  # type: ignore[misc]


def test_snapshot_helpers() -> None:
    operable = Element(ref="e1", role="Button", name="Ok")
    hidden = Element(ref="e2", role="Text", name="no", interactable=False)
    snap = Snapshot(snapshot_id="s1", window=_window(), elements=(operable, hidden))
    assert snap.find("e1") == operable
    assert snap.find("nope") is None
    assert snap.operable_elements() == (operable,)
    assert snap.signature() == snap.signature()
    changed = Snapshot(
        snapshot_id="s1",
        window=_window(),
        elements=(Element(ref="e1", role="Button", name="Cancelar"), hidden),
    )
    assert changed.signature() != snap.signature()


def test_backend_abc_is_not_instantiable() -> None:
    with pytest.raises(TypeError):
        DesktopBackend()  # type: ignore[abstract]


def test_backend_subclass_instantiable_and_async() -> None:
    class Dummy(DesktopBackend):
        @property
        def capabilities(self) -> Capabilities:
            return Capabilities(platform="dummy")

        async def list_windows(self) -> list[WindowInfo]:
            return []

        async def observe(self, window, limits=None) -> Snapshot:
            return Snapshot(snapshot_id="s", window=_window())

        async def act(self, snapshot_id, action) -> ActResult:
            return ActResult()

    dummy = Dummy()
    assert dummy.capabilities.platform == "dummy"
    assert inspect.iscoroutinefunction(Dummy.list_windows)
    assert inspect.iscoroutinefunction(Dummy.observe)
    assert inspect.iscoroutinefunction(Dummy.act)
    assert asyncio.run(dummy.list_windows()) == []
