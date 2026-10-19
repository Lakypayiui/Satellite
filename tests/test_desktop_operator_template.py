"""Plantilla estática desktop_operator (F5)."""

from satellite.agents.prompts import find_placeholders
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.templates import (
    DESKTOP_OPERATOR_ID,
    desktop_operator_template,
    get_template,
    has_template,
    template_ids,
)
from satellite.agents.validator import AgentProposalValidator
from satellite.desktop_control import ALLOWED_ACTIONS
from satellite.tool_manifest import TOOL_IDS


def test_template_identity_tools_and_examples() -> None:
    template = desktop_operator_template()
    assert template.id == DESKTOP_OPERATOR_ID
    assert template.name == "Desktop Operator"
    assert set(template.tools) == set(TOOL_IDS)
    assert len(template.capabilities) >= 2
    assert len(template.example_tasks) >= 3
    assert template.output_schema is not None
    assert has_template("desktop_operator")
    assert has_template("  DESKTOP_OPERATOR ")
    assert get_template("no_existe") is None
    assert template_ids() == ("desktop_operator",)


def test_output_schema_is_exactly_the_addendum_keys() -> None:
    schema = desktop_operator_template().output_schema
    assert isinstance(schema, dict)
    assert set(schema["properties"]) == {"action", "target", "text", "snapshot_id"}
    assert schema["required"] == ["action", "target", "text", "snapshot_id"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == list(ALLOWED_ACTIONS)


def test_system_prompt_is_static_and_treats_ui_as_untrusted_data() -> None:
    template = desktop_operator_template()
    assert find_placeholders(template.system_prompt) == []
    assert "{" not in template.system_prompt
    assert "UNTRUSTED" in template.system_prompt
    assert "<data>" in template.system_prompt
    assert "snapshot_id" in template.system_prompt
    # La parte dinámica va en el user template; el snapshot va AL FINAL en <data>.
    placeholders = find_placeholders(template.user_template)
    assert placeholders[-1] == "context"
    assert set(placeholders) == {"task", "context", "feedback"}
    assert template.user_template.rstrip().endswith("</data>")
    assert '<data source="snapshot">' in template.user_template


def _validator(tool_ids: set[str]) -> AgentProposalValidator:
    return AgentProposalValidator(
        DefaultAgentRegistry(), tool_ids=tool_ids, allowed_roles=("generated",)
    )


def test_template_passes_the_deterministic_validator() -> None:
    report = _validator(set(TOOL_IDS)).validate(desktop_operator_template())
    assert report.ok is True, report.errors


def test_template_is_rejected_without_desktop_tools() -> None:
    report = _validator({"read_file"}).validate(desktop_operator_template())
    assert report.ok is False
    assert any("unknown tool" in error for error in report.errors)
