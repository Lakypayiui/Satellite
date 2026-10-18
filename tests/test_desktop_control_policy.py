"""Política de control de escritorio, config local, grants, topes y emergencia."""

import asyncio

from satellite.approval.base import ApprovalContext
from satellite.approval.policy import ApprovalPolicy
from satellite.core.proposal import ApprovalStatus
from satellite.desktop_control import (
    SCOPE_CONTROL,
    SCOPE_OBSERVE,
    ActionBudget,
    DesktopControlPolicy,
    DesktopControlSettings,
    DesktopGrantStore,
    Element,
    EmergencyStop,
    classify_destructive,
    load_desktop_settings,
    normalize_app,
)
from satellite.kernel.policy import PolicyMode


def _settings(**overrides) -> DesktopControlSettings:
    data = dict(
        enabled=True,
        allowed_processes=("demo",),
        denied_apps=(),
        max_actions_per_run=20,
        max_seconds_per_run=120.0,
        rate_per_minute=30,
    )
    data.update(overrides)
    return DesktopControlSettings(**data)


# --- configuración local ----------------------------------------------------


def test_settings_defaults_are_deny_by_default() -> None:
    settings = DesktopControlSettings()
    assert settings.enabled is False
    assert settings.allowed_processes == ()
    assert settings.allows_app("demo") is False
    assert "keepass" in settings.denied_apps
    assert "windowsterminal" in settings.denied_apps


def test_normalize_app() -> None:
    assert normalize_app("C:\\Program Files\\Demo\\Demo.EXE") == "demo"
    assert normalize_app("/usr/bin/demo") == "demo"
    assert normalize_app("  Demo   App ") == "demo app"


def test_load_missing_and_corrupt_are_denied(tmp_path) -> None:
    assert load_desktop_settings(tmp_path / "nope.json").enabled is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_desktop_settings(bad).enabled is False


def test_load_reads_allowlist_and_defaults_denylist(tmp_path) -> None:
    path = tmp_path / "desktop_control.json"
    path.write_text(
        '{"enabled": true, "allowed_processes": ["Demo.exe"], '
        '"max_actions_per_run": 3, "rate_per_minute": 2, "max_seconds_per_run": 5}',
        encoding="utf-8",
    )
    settings = load_desktop_settings(path)
    assert settings.enabled is True
    assert settings.allowed_processes == ("demo",)
    assert settings.allows_app("DEMO") is True
    assert settings.max_actions_per_run == 3
    # denylist por defecto si el archivo no la trae
    assert "keepass" in settings.denied_apps


def test_settings_denylist_wins() -> None:
    settings = _settings(allowed_processes=("demo",), denied_apps=("demo",))
    assert settings.allows_app("demo") is False


# --- grants -----------------------------------------------------------------


def test_grants_are_exact_scoped_and_revocable() -> None:
    store = DesktopGrantStore()
    store.grant("s1", "Demo.exe", SCOPE_OBSERVE)
    assert store.is_granted("s1", "demo", SCOPE_OBSERVE) is True
    assert store.is_granted("s2", "demo", SCOPE_OBSERVE) is False
    assert store.is_granted("s1", "otra", SCOPE_OBSERVE) is False
    assert store.is_granted("s1", "demo", SCOPE_CONTROL) is False
    assert store.revoke("s1", "demo", SCOPE_OBSERVE) == 1
    assert store.is_granted("s1", "demo", SCOPE_OBSERVE) is False


def test_grant_with_empty_fields_is_ignored() -> None:
    store = DesktopGrantStore()
    store.grant("", "demo", SCOPE_OBSERVE)
    store.grant("s1", "", SCOPE_OBSERVE)
    assert store.entries() == []


# --- emergencia / topes -----------------------------------------------------


def test_emergency_stop() -> None:
    stop = EmergencyStop()
    assert stop.tripped is False
    stop.engage("manual")
    assert stop.tripped is True and stop.reason == "manual"
    stop.clear()
    assert stop.tripped is False


def test_budget_actions_time_and_rate() -> None:
    clock = [0.0]
    settings = _settings(
        max_actions_per_run=2, max_seconds_per_run=10.0, rate_per_minute=5
    )
    budget = ActionBudget(settings, clock=lambda: clock[0])
    assert budget.check().allowed is True
    budget.note()
    budget.note()
    assert budget.check().code == "action_budget"

    budget.reset_run()
    clock[0] = 11.0
    assert budget.check().code == "time_budget"

    limited = ActionBudget(
        _settings(max_actions_per_run=10, max_seconds_per_run=100.0, rate_per_minute=1),
        clock=lambda: clock[0],
    )
    limited.note()
    assert limited.check().code == "rate_limited"


# --- clasificación destructiva ---------------------------------------------


def test_destructive_classification_is_strict_and_conservative() -> None:
    assert (
        classify_destructive(Element(ref="e", role="Button", name="Eliminar"), "click")
        == "destructive"
    )
    assert (
        classify_destructive(
            Element(ref="e", role="Button", automation_id="deleteButton"), "click"
        )
        == "destructive"
    )
    assert (
        classify_destructive(Element(ref="e", role="Edit", name="Nombre"), "set_value")
        == "safe"
    )
    # Un botón genérico no se puede declarar inocuo ⇒ unknown (confirmar).
    assert (
        classify_destructive(Element(ref="e", role="Button", name="Guardar"), "click")
        == "unknown"
    )
    assert (
        classify_destructive(Element(ref="e", role="Button", name="X"), "focus") == "safe"
    )
    assert (
        classify_destructive(
            Element(ref="e", role="Edit", name="Contraseña", sensitive=True), "type"
        )
        == "destructive"
    )


# --- gates ------------------------------------------------------------------


def test_app_decision_disabled_denied_and_allowlisted() -> None:
    disabled = DesktopControlPolicy(DesktopControlSettings())
    assert disabled.app_decision("demo").code == "desktop_disabled"

    policy = DesktopControlPolicy(_settings(allowed_processes=("demo",), denied_apps=("bad",)))
    assert policy.app_decision("demo").allowed is True
    assert policy.app_decision("bad").code == "app_denied"
    assert policy.app_decision("otra").code == "app_not_allowed"
    assert policy.app_decision("").code == "invalid_app"
    assert policy.app_decision("*").allowed is True


def test_check_screen_requires_grant() -> None:
    policy = DesktopControlPolicy(_settings())
    decision = policy.check_screen("s1", "demo", SCOPE_OBSERVE)
    assert decision.allowed is False
    assert decision.needs_approval is True
    assert decision.code == "approval_required"
    policy.grant("s1", "demo", SCOPE_OBSERVE)
    assert policy.check_screen("s1", "demo", SCOPE_OBSERVE).allowed is True


def test_check_action_grant_then_confirmation() -> None:
    policy = DesktopControlPolicy(_settings())
    edit = Element(ref="e1", role="Edit", name="Nombre")
    pending = policy.check_action("set_value", edit, "demo", "s1")
    assert pending.needs_approval is True

    policy.grant("s1", "demo", SCOPE_CONTROL)
    assert policy.check_action("set_value", edit, "demo", "s1").allowed is True

    button = Element(ref="e2", role="Button", name="Guardar")
    confirm = policy.check_action("click", button, "demo", "s1")
    assert confirm.allowed is False
    assert confirm.needs_confirmation is True
    assert confirm.code == "confirmation_required"


def test_check_action_sensitive_emergency_and_budget() -> None:
    policy = DesktopControlPolicy(_settings())
    policy.grant("s1", "demo", SCOPE_CONTROL)
    password = Element(ref="e", role="Edit", name="Contraseña", sensitive=True)
    assert (
        policy.check_action("set_value", password, "demo", "s1").code
        == "sensitive_target"
    )

    policy.emergency.engage("test")
    assert (
        policy.check_action("set_value", Element(ref="e", role="Edit"), "demo", "s1").code
        == "emergency_stop"
    )
    policy.emergency.clear()

    limited = DesktopControlPolicy(_settings(max_actions_per_run=1))
    limited.grant("s1", "demo", SCOPE_CONTROL)
    limited.note_action()
    assert (
        limited.check_action("set_value", Element(ref="e", role="Edit"), "demo", "s1").code
        == "action_budget"
    )


# --- política de aprobación: nunca auto -------------------------------------


def test_requires_human_is_never_auto_approved() -> None:
    for mode in (PolicyMode.AUTO, PolicyMode.UNRESTRICTED):
        policy = ApprovalPolicy(mode)
        assert policy.pre_decide(requires_human=True) is None


def test_requires_human_without_port_is_rejected() -> None:
    decision = asyncio.run(
        ApprovalPolicy(PolicyMode.UNRESTRICTED).decide(
            object(),
            ApprovalContext(kind="desktop_control"),
            None,
            requires_human=True,
        )
    )
    assert decision.status is ApprovalStatus.REJECTED
    assert "puerto" in decision.reason
