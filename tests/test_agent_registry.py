"""Tests de DefaultAgentRegistry."""

import json
from pathlib import Path

import pytest

from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.errors import SpecValidationError
from satellite.core.spec import AgentSpec


def make_spec(spec_id="html_writer", **overrides) -> AgentSpec:
    kwargs = dict(id=spec_id, name="Test", capabilities=("write_html",), prompt_ref="html_writer")
    kwargs.update(overrides)
    return AgentSpec(**kwargs)


def test_register_and_get() -> None:
    reg = DefaultAgentRegistry()
    reg.register_spec(make_spec("a"))
    assert reg.get("a").id == "a"
    with pytest.raises(KeyError):
        reg.get("no_existe")


def test_register_duplicate_raises() -> None:
    reg = DefaultAgentRegistry()
    reg.register_spec(make_spec("a"))
    with pytest.raises(SpecValidationError):
        reg.register_spec(make_spec("a"))


def test_find_by_capability() -> None:
    reg = DefaultAgentRegistry()
    reg.register_spec(make_spec("html_writer", capabilities=("write_html", "modify_html"), description="Especialista en crear y modificar documentos HTML"))
    reg.register_spec(make_spec("test_runner", capabilities=("run_tests",), description="Ejecuta tests"))
    found = reg.find_by_capability("crear html")
    assert [s.id for s in found] == ["html_writer"]


def test_all() -> None:
    reg = DefaultAgentRegistry()
    reg.register_spec(make_spec("a"))
    reg.register_spec(make_spec("b"))
    assert {s.id for s in reg.all()} == {"a", "b"}


def test_load_dir(tmp_path: Path) -> None:
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    spec = {
        "id": "html_writer",
        "name": "HTML Writer",
        "description": "Escribe HTML",
        "capabilities": ["write_html"],
        "prompt_ref": "html_writer",
        "tools": [],
    }
    (specs_dir / "html_writer.json").write_text(json.dumps(spec), encoding="utf-8")
    reg = DefaultAgentRegistry()
    reg.load_dir(specs_dir)
    assert reg.get("html_writer").capabilities == ("write_html",)


def test_load_dir_override() -> None:
    reg = DefaultAgentRegistry()
    reg.register_spec(make_spec("a", description="original"))
    assert reg.get("a").description == "original"
    # override: misma id, nueva descripción
    reg._specs["a"] = make_spec("a", description="sobrescrito")
    assert reg.get("a").description == "sobrescrito"
