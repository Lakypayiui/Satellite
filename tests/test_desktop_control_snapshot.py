"""Serialización compacta, límites, diff, quality y saneamiento (F1)."""

import asyncio

from satellite.desktop_control import (
    Action,
    Element,
    FakeBackend,
    ObserveLimits,
    WindowInfo,
    build_snapshot,
    render_element,
)
from satellite.desktop_control.fake import demo_elements


def run(coro):
    return asyncio.run(coro)


def test_text_only_includes_operable_elements() -> None:
    snap = run(FakeBackend().observe("win-main"))
    assert "[e2] Button \"Guardar\"" in snap.text
    assert "[e10]" in snap.text
    # No operables (ventana, deshabilitado, texto) quedan fuera del texto.
    assert "[e1]" not in snap.text
    assert "[e6]" not in snap.text
    assert "[e9]" not in snap.text


def test_sensitive_value_is_never_rendered() -> None:
    sensitive = next(e for e in demo_elements() if e.sensitive)
    line = render_element(sensitive)
    assert "hunter2" not in line
    assert "(sensible" in line
    snap = run(FakeBackend().observe("win-main"))
    assert "hunter2" not in snap.text


def test_header_marks_active_window() -> None:
    snap = run(FakeBackend().observe("win-main"))
    assert snap.text.splitlines()[0] == 'ventana: "Editor — Documento sin título" · Demo App · activa'


def test_limits_truncate_elements() -> None:
    snap = run(FakeBackend().observe("win-main", ObserveLimits(max_elements=2)))
    assert snap.truncated is True
    assert "elementos no mostrados" in snap.text


def test_limits_truncate_chars() -> None:
    snap = run(FakeBackend().observe("win-main", ObserveLimits(max_chars=20)))
    assert snap.truncated is True
    assert snap.text.endswith("[truncado]")


def test_quality_good_and_poor() -> None:
    backend = FakeBackend()
    good = run(backend.observe("win-main"))
    assert good.quality == "good" and good.reason == ""
    poor = run(backend.observe("win-prefs"))
    assert poor.quality == "poor" and poor.reason


def test_diff_reports_change() -> None:
    backend = FakeBackend()
    first = run(backend.observe("win-main"))
    run(backend.act(first.snapshot_id, Action(kind="set_value", target="e3", text="Bea")))
    second = run(backend.observe("win-main", ObserveLimits(mode="diff")))
    assert second.mode == "diff"
    assert second.previous_snapshot_id == first.snapshot_id
    assert "~ [e3]" in second.text
    assert "[e2]" not in second.text  # sin cambios: se compacta


def test_diff_reports_no_changes() -> None:
    backend = FakeBackend()
    run(backend.observe("win-main"))
    second = run(backend.observe("win-main", ObserveLimits(mode="diff")))
    assert "(sin cambios)" in second.text


def test_diff_without_previous_falls_back_to_full() -> None:
    window = WindowInfo(id="w", title="T")
    snap = build_snapshot(
        snapshot_id="s1",
        window=window,
        elements=(Element(ref="e1", role="Button", name="Ok"),),
        limits=ObserveLimits(mode="diff"),
        previous=None,
    )
    assert snap.mode == "full"


def test_diff_reports_added_and_removed() -> None:
    window = WindowInfo(id="w", title="T")
    first = build_snapshot(
        snapshot_id="s1",
        window=window,
        elements=(Element(ref="e1", role="Button", name="A"),),
    )
    diff = build_snapshot(
        snapshot_id="s2",
        window=window,
        elements=(Element(ref="e2", role="Button", name="B"),),
        limits=ObserveLimits(mode="diff"),
        previous=first,
    )
    assert "+ [e2]" in diff.text
    assert "- [e1]" in diff.text
