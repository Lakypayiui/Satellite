"""Validador determinista de acciones y guardián de ejecución (F1)."""

import asyncio

from satellite.desktop_control import (
    ACTION_NOT_ALLOWED,
    COORDINATES_DISABLED,
    SENSITIVE_TARGET,
    SNAPSHOT_OBSOLETE,
    TARGET_MISSING,
    TARGET_NOT_INTERACTABLE,
    TEXT_TOO_LONG,
    Action,
    FakeBackend,
    apply_action,
    validate_action,
)


def run(coro):
    return asyncio.run(coro)


def _last() -> object:
    return run(FakeBackend().observe("win-main"))


def test_allowlist_closed_rejects_unknown_action() -> None:
    snap = _last()
    decision = validate_action(Action(kind="exec"), snapshot=snap, snapshot_id=snap.snapshot_id)
    assert decision.ok is False
    assert decision.code == ACTION_NOT_ALLOWED


def test_valid_click_returns_element() -> None:
    snap = _last()
    decision = validate_action(
        Action(kind="click", target="e2"), snapshot=snap, snapshot_id=snap.snapshot_id
    )
    assert decision.ok is True
    assert decision.element is not None and decision.element.ref == "e2"


def test_stale_or_missing_snapshot_is_rejected() -> None:
    snap = _last()
    for sid in ("otro", ""):
        decision = validate_action(
            Action(kind="click", target="e2"), snapshot=snap, snapshot_id=sid
        )
        assert decision.code == SNAPSHOT_OBSOLETE
    assert (
        validate_action(
            Action(kind="click", target="e2"), snapshot=None, snapshot_id="x"
        ).code
        == SNAPSHOT_OBSOLETE
    )


def test_target_required_and_must_exist() -> None:
    snap = _last()
    assert (
        validate_action(Action(kind="click"), snapshot=snap, snapshot_id=snap.snapshot_id).code
        == TARGET_MISSING
    )
    assert (
        validate_action(
            Action(kind="click", target="e99"), snapshot=snap, snapshot_id=snap.snapshot_id
        ).code
        == TARGET_MISSING
    )


def test_target_must_be_visible_and_interactable() -> None:
    snap = _last()
    assert (
        validate_action(
            Action(kind="click", target="e1"), snapshot=snap, snapshot_id=snap.snapshot_id
        ).code
        == TARGET_NOT_INTERACTABLE
    )
    assert (
        validate_action(
            Action(kind="click", target="e6"), snapshot=snap, snapshot_id=snap.snapshot_id
        ).code
        == TARGET_NOT_INTERACTABLE
    )


def test_text_is_bounded() -> None:
    snap = _last()
    decision = validate_action(
        Action(kind="type", target="e3", text="x" * 50),
        snapshot=snap,
        snapshot_id=snap.snapshot_id,
        max_text_chars=10,
    )
    assert decision.code == TEXT_TOO_LONG


def test_coordinates_only_with_fallback_enabled() -> None:
    snap = _last()
    action = Action(kind="click", target="e2", coordinates=(10, 20))
    assert (
        validate_action(action, snapshot=snap, snapshot_id=snap.snapshot_id).code
        == COORDINATES_DISABLED
    )
    assert validate_action(
        action, snapshot=snap, snapshot_id=snap.snapshot_id, allow_coordinates=True
    ).ok


def test_sensitive_field_is_never_written() -> None:
    snap = _last()
    decision = validate_action(
        Action(kind="set_value", target="e4", text="x"),
        snapshot=snap,
        snapshot_id=snap.snapshot_id,
    )
    assert decision.code == SENSITIVE_TARGET
    # Leer/clickar el campo sensible no escribe: se permite.
    assert validate_action(
        Action(kind="click", target="e4"), snapshot=snap, snapshot_id=snap.snapshot_id
    ).ok


def test_key_without_target_is_allowed() -> None:
    snap = _last()
    assert validate_action(
        Action(kind="key", text="Tab"), snapshot=snap, snapshot_id=snap.snapshot_id
    ).ok
    assert (
        validate_action(
            Action(kind="key", target="e99"), snapshot=snap, snapshot_id=snap.snapshot_id
        ).code
        == TARGET_MISSING
    )


def test_apply_action_does_not_execute_when_invalid() -> None:
    backend = FakeBackend()
    snap = run(backend.observe("win-main"))
    result = run(
        apply_action(
            backend,
            snapshot=snap,
            snapshot_id="obsoleto",
            action=Action(kind="click", target="e2"),
        )
    )
    assert result.ok is False and result.code == SNAPSHOT_OBSOLETE
    assert backend.calls == []


def test_apply_action_executes_and_reobserves() -> None:
    backend = FakeBackend()
    snap = run(backend.observe("win-main"))
    result = run(
        apply_action(
            backend, snapshot=snap, action=Action(kind="set_value", target="e3", text="Bea")
        )
    )
    assert result.ok is True
    assert result.changed is True
    assert result.after_snapshot_id and result.after_snapshot_id != snap.snapshot_id
    assert len(backend.calls) == 1


def test_apply_action_sensitive_is_denied_without_calling_backend() -> None:
    backend = FakeBackend()
    snap = run(backend.observe("win-main"))
    result = run(
        apply_action(
            backend, snapshot=snap, action=Action(kind="set_value", target="e4", text="x")
        )
    )
    assert result.ok is False and result.code == SENSITIVE_TARGET
    assert backend.calls == []


def test_backend_refuses_sensitive_write_even_if_bypassed() -> None:
    backend = FakeBackend()
    result = run(backend.act("cualquiera", Action(kind="set_value", target="e4", text="x")))
    assert result.ok is False and result.code == "sensitive_target"
