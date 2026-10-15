"""Tests de DefaultPromptRegistry."""

from pathlib import Path

import pytest

from satellite.agents.prompts import DefaultPromptRegistry


def test_register_and_get() -> None:
    reg = DefaultPromptRegistry()
    reg.register("p1", "hola")
    assert reg.get("p1") == "hola"
    with pytest.raises(KeyError):
        reg.get("no_existe")


def test_load_dir(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("plantilla A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("plantilla B", encoding="utf-8")
    reg = DefaultPromptRegistry()
    reg.load_dir(tmp_path)
    assert reg.get("a") == "plantilla A"
    assert reg.get("b") == "plantilla B"


def test_render_with_placeholders() -> None:
    reg = DefaultPromptRegistry()
    reg.register("p", "Tarea: {task} | Contexto: {context}")
    out = reg.render("p", task="crear html", context="src/")
    assert out == "Tarea: crear html | Contexto: src/"


def test_render_missing_placeholder_empty() -> None:
    reg = DefaultPromptRegistry()
    reg.register("p", "A={a} B={b}")
    out = reg.render("p", a="1")  # b faltante
    assert out == "A=1 B="


def test_pair_registry_and_load_dir(tmp_path: Path) -> None:
    (tmp_path / "a.system.txt").write_text("Sistema A", encoding="utf-8")
    (tmp_path / "a.user.txt").write_text("Tarea: {task}", encoding="utf-8")
    (tmp_path / "b.system.txt").write_text("Sistema B", encoding="utf-8")
    reg = DefaultPromptRegistry()
    reg.load_dir(tmp_path)
    assert reg.get_system("a") == "Sistema A"
    assert reg.get_user("a") == "Tarea: {task}"
    assert reg.get("a") == "Sistema A"  # compat: get devuelve el system
    assert reg.get_user("b") == ""  # sin user file


def test_register_pair_and_legacy_flag(tmp_path: Path) -> None:
    (tmp_path / "legacy.txt").write_text("viejo", encoding="utf-8")
    reg = DefaultPromptRegistry()
    reg.load_dir(tmp_path)
    assert reg.get_system("legacy") == "viejo"
    assert "legacy" in reg.legacy
    reg.register_pair("nuevo", "S", "Tarea: {task}")
    assert reg.get_user("nuevo") == "Tarea: {task}"
    assert "nuevo" not in reg.legacy
