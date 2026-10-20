"""Tests del catálogo base (agentes del kernel + agente de diagrama) y prompts (Fase 5)."""

from pathlib import Path

from satellite.agents.prompts import (
    ALLOWED_PLACEHOLDERS,
    USER_PLACEHOLDERS,
    DefaultPromptRegistry,
    find_placeholders,
    render_system,
    render_template,
)
from satellite.agents.registry import DefaultAgentRegistry
from satellite.kernel.manifest import RESERVED_AGENT_IDS

PKG = Path(__file__).resolve().parent.parent / "satellite" / "agents"
SPECS = PKG / "specs"
PROMPTS = PKG / "prompts"
EXAMPLES = PKG / "examples"

SHIPPED_PROMPTS = (
    "orchestrator",
    "agent_creator",
    "skill_curator",
    "plan_options",
    "pele_diagram_agent",
    "desktop_operator",
    "web_researcher",
)


def test_base_specs_are_the_shipped_agents() -> None:
    reg = DefaultAgentRegistry()
    reg.load_dir(SPECS)
    assert {spec.id for spec in reg.all()} == {
        "orchestrator",
        "agent_creator",
        "skill_curator",
        "pele_diagram_agent",
        "desktop_operator",
        "web_researcher",
    }


def test_web_researcher_is_a_primitive_agent() -> None:
    reg = DefaultAgentRegistry()
    reg.load_dir(SPECS)
    spec = reg.get("web_researcher")
    assert spec.tools == ("web_search", "web_fetch", "web_download")
    assert {"web_search", "web_research"} <= set(spec.capabilities)
    assert spec.output_schema is not None
    assert "sources" in spec.output_schema["required"]


def test_desktop_operator_is_a_primitive_agent() -> None:
    reg = DefaultAgentRegistry()
    reg.load_dir(SPECS)
    spec = reg.get("desktop_operator")
    assert spec.tools == (
        "desktop_list_windows",
        "desktop_observe",
        "desktop_act",
        "run_command",
    )
    assert {"observe_desktop", "act_desktop"} <= set(spec.capabilities)
    assert spec.output_schema is not None
    assert "summary" in spec.output_schema["required"]


def test_base_specs_model_roles() -> None:
    reg = DefaultAgentRegistry()
    reg.load_dir(SPECS)
    assert reg.get("orchestrator").model_role == "orchestrator"
    assert reg.get("agent_creator").model_role == "creator"
    assert reg.get("skill_curator").model_role == "curator"


def test_base_ids_are_reserved() -> None:
    reg = DefaultAgentRegistry()
    reg.load_dir(SPECS)
    assert all(spec.id in RESERVED_AGENT_IDS for spec in reg.all())


def test_base_prompts_use_only_allowed_placeholders() -> None:
    prompts = DefaultPromptRegistry()
    prompts.load_dir(PROMPTS)
    for prompt_id in SHIPPED_PROMPTS:
        system = prompts.get_system(prompt_id)
        user = prompts.get_user(prompt_id)
        assert system.strip() and user.strip()
        for placeholder in find_placeholders(system):
            assert placeholder in ALLOWED_PLACEHOLDERS, (prompt_id, placeholder)
        # el user sólo lleva la parte dinámica, con {task} obligatorio
        for placeholder in find_placeholders(user):
            assert placeholder in USER_PLACEHOLDERS, (prompt_id, placeholder)
        assert "task" in find_placeholders(user)


def test_no_retrieved_data_in_system() -> None:
    """El catálogo/contexto NO se inyectan en el system: van como bloque USER."""
    prompts = DefaultPromptRegistry()
    prompts.load_dir(PROMPTS)
    for prompt_id in SHIPPED_PROMPTS:
        system = prompts.get_system(prompt_id)
        # el bloque de datos recuperados nunca va en el system (sólo se menciona
        # en la documentación, que no es contenido inyectado)
        assert 'source="catalogo"' not in system
        assert "«" not in system


def test_orchestrator_prompt_declares_three_outputs() -> None:
    prompts = DefaultPromptRegistry()
    prompts.load_dir(PROMPTS)
    text = prompts.get("orchestrator")
    for token in ("capability_gap", "needs_clarification", '"type":"plan"'):
        assert token in text


def test_demo_specs_are_not_loaded_from_specs_dir() -> None:
    for demo in ("html_writer", "html_reviewer", "test_runner", "code_analyzer"):
        assert not (SPECS / f"{demo}.json").exists()
        assert (EXAMPLES / f"{demo}.json").exists()


def test_base_prompts_render_with_distinct_values() -> None:
    prompts = DefaultPromptRegistry()
    prompts.load_dir(PROMPTS)
    values = {
        "task": "«TAREA»",
        "context": "«CONTEXTO»",
        "feedback": "«FEEDBACK»",
        "output_schema": "«SCHEMA»",
        "tools": "«TOOLS»",
    }
    for prompt_id in SHIPPED_PROMPTS:
        # el system sólo se renderiza con {tools}/{output_schema}: lo dinámico
        # NUNCA entra, aunque el texto mencione los placeholders
        system = render_system(
            prompts.get_system(prompt_id), tools="«TOOLS»", output_schema="«SCHEMA»"
        )
        user = render_template(prompts.get_user(prompt_id), values)
        assert "«TAREA»" not in system
        assert "«CONTEXTO»" not in system
        assert "«FEEDBACK»" not in system
        assert "«TAREA»" in user
        assert "«CONTEXTO»" in user
        assert "«FEEDBACK»" in user
        for name in ("task", "context", "feedback"):
            assert "{" + name + "}" not in user
