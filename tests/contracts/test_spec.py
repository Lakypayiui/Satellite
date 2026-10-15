"""Tests de contrato de AgentSpec."""

import dataclasses
import json
from pathlib import Path

import pytest

from satellite.core.spec import AgentSpec
from satellite.core.errors import SpecValidationError

FIXTURE = Path(__file__).parent / "fixtures" / "html_writer.spec.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_from_dict() -> None:
    spec = AgentSpec.from_dict(load_fixture())
    assert spec.id == "html_writer"
    assert spec.name == "HTML Writer"
    assert spec.description == "Agente especializado en crear y modificar documentos HTML"
    assert spec.capabilities == ("write_html", "modify_html")
    assert spec.prompt_ref == "html_writer"
    assert spec.tools == ("read_file", "write_file", "edit_file")
    assert spec.validators == ()
    assert spec.model_role == "agent"
    assert spec.max_turns == 1
    assert spec.timeout_s == 120


def test_round_trip() -> None:
    spec = AgentSpec.from_dict(load_fixture())
    restored = AgentSpec.from_dict(spec.to_dict())
    assert restored == spec


def test_id_empty_raises() -> None:
    with pytest.raises(SpecValidationError):
        AgentSpec(id="", name="X", capabilities=("a",), prompt_ref="p")


def test_name_empty_raises() -> None:
    with pytest.raises(SpecValidationError):
        AgentSpec(id="x", name="", capabilities=("a",), prompt_ref="p")


def test_capabilities_empty_raises() -> None:
    with pytest.raises(SpecValidationError):
        AgentSpec(id="x", name="X", capabilities=(), prompt_ref="p")


def test_capabilities_empty_string_raises() -> None:
    with pytest.raises(SpecValidationError):
        AgentSpec(id="x", name="X", capabilities=("a", ""), prompt_ref="p")


def test_prompt_ref_empty_raises() -> None:
    with pytest.raises(SpecValidationError):
        AgentSpec(id="x", name="X", capabilities=("a",), prompt_ref="")


def test_max_turns_negative_raises() -> None:
    with pytest.raises(SpecValidationError):
        AgentSpec(id="x", name="X", capabilities=("a",), prompt_ref="p", max_turns=-1)


def test_timeout_zero_raises() -> None:
    with pytest.raises(SpecValidationError):
        AgentSpec(id="x", name="X", capabilities=("a",), prompt_ref="p", timeout_s=0)


def test_frozen() -> None:
    spec = AgentSpec(id="x", name="X", capabilities=("a",), prompt_ref="p")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "otro"
