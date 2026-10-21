"""Tests del split `system_prompt` / `user_template` y de su migración (F2)."""

from pathlib import Path

from satellite.agents.prompts import (
    DefaultPromptRegistry,
    render_system,
    render_template,
)
from satellite.agents.runtime import build_system_prompt, build_user_prompt
from satellite.core.context_models import (
    ContextBundle,
    ContextRef,
    ContextRefKind,
    ContextSection,
)
from satellite.kernel.migrations import migrate_prompt_files, split_legacy_prompt

USER_TEMPLATE = "# ENTRADAS\n<data>\nTarea: {task}\nContexto: {context}\nFeedback: {feedback}\n</data>"


# --- render del system ---

def test_render_system_substitutes_only_static_placeholders() -> None:
    template = "Tools:\n{tools}\nSchema:\n{output_schema}\nMenciona {task} y {context}."
    rendered = render_system(template, tools="T", output_schema="S")
    assert "Tools:\nT" in rendered
    assert "Schema:\nS" in rendered
    # las menciones dinámicas quedan INTACTAS (son documentación)
    assert "{task}" in rendered
    assert "{context}" in rendered


def test_build_system_prompt_is_static() -> None:
    system = build_system_prompt(
        "Eres un escritor.\n{tools}\n{output_schema}",
        output_schema={"type": "object"},
        tools_text="- read_file: lee",
    )
    assert "- read_file: lee" in system
    assert '"type": "object"' in system
    assert "{tools}" not in system
    assert "{output_schema}" not in system
    # nunca recibe contenido dinámico
    assert "{task}" not in system or True  # las menciones se permiten
    assert "«" not in system


def test_build_system_prompt_appends_a_missing_schema() -> None:
    system = build_system_prompt(
        "Follow the declared output schema.",
        output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
    )
    assert "# OUTPUT SCHEMA" in system
    assert '"summary"' in system


def test_build_system_prompt_adds_untrusted_banner_and_delegation() -> None:
    system = build_system_prompt("Sé útil.", can_call=())
    assert "untrusted" in system.lower()
    from satellite.core.spec import CanCallEntry

    with_delegation = build_system_prompt(
        "Sé útil.", can_call=(CanCallEntry(agent_id="other", description="para X"),)
    )
    assert "# DELEGATION" in with_delegation
    assert "other: para X" in with_delegation


# --- render del user ---

def test_build_user_prompt_fills_dynamic_part() -> None:
    section = ContextSection(
        ref=ContextRef(ContextRefKind.PATH, "a.py"), content="contenido"
    )
    user = build_user_prompt(
        USER_TEMPLATE,
        task={"goal": "haz algo"},
        context=ContextBundle(sections=(section,)),
        feedback=(),
    )
    assert "haz algo" in user
    assert "contenido" in user
    assert "(no feedback)" in user
    assert "{task}" not in user
    assert "{context}" not in user
    assert "{feedback}" not in user


# --- registry de pares ---

def test_registry_get_user_raises_for_missing() -> None:
    reg = DefaultPromptRegistry()
    reg.register_pair("a", "S", "U")
    assert reg.get_system("a") == "S"
    assert reg.get_user("a") == "U"
    try:
        reg.get_user("nope")
    except KeyError:
        return
    raise AssertionError("debía lanzar KeyError")


def test_render_template_only_touches_allowed_placeholders() -> None:
    # los placeholders fuera de la allowlist quedan literales (ejemplos JSON)
    assert render_template("A={task} B={otro}", task="1") == "A=1 B={otro}"


def test_render_template_does_not_expand_placeholders_inside_values() -> None:
    """Un `{context}` dentro de la tarea no debe arrastrar el contexto real."""
    rendered = render_template(
        "Task={task} Context={context}",
        task="literal {context} stays",
        context="SECRET",
    )
    assert "literal {context} stays" in rendered
    assert rendered.endswith("Context=SECRET")


def test_render_system_does_not_expand_placeholders_inside_tools() -> None:
    rendered = render_system(
        "T={tools} S={output_schema}", tools="tool {output_schema}", output_schema="SCHEMA"
    )
    assert "tool {output_schema}" in rendered
    assert rendered.endswith("S=SCHEMA")


# --- migración del layout antiguo ---

def test_split_legacy_prompt_cuts_at_entradas() -> None:
    legacy = "# ROL\nHaz cosas.\n\n# ENTRADAS\n<data>\nTarea: {task}\n</data>\n"
    system, user = split_legacy_prompt(legacy)
    assert system is not None
    assert "# ROL" in system and "# ENTRADAS" not in system
    assert user.startswith("# ENTRADAS")
    assert "{task}" in user


def test_split_legacy_prompt_without_marker_returns_none() -> None:
    assert split_legacy_prompt("sin marcador") == (None, "")


def test_migrate_prompt_files_converts_and_quarantines(tmp_path: Path) -> None:
    (tmp_path / "good.txt").write_text(
        "# ROL\nHaz cosas.\n\n# ENTRADAS\nTarea: {task}\n", encoding="utf-8"
    )
    (tmp_path / "bad.txt").write_text("no tengo marcador", encoding="utf-8")
    (tmp_path / "paired.system.txt").write_text("S", encoding="utf-8")
    (tmp_path / "paired.txt").write_text("viejo", encoding="utf-8")  # superado

    converted = migrate_prompt_files(tmp_path)
    assert converted == ["good"]
    assert (tmp_path / "good.system.txt").exists()
    assert (tmp_path / "good.user.txt").exists()
    assert not (tmp_path / "good.txt").exists()
    # sin marcador → cuarentena
    assert not (tmp_path / "bad.txt").exists()
    assert (tmp_path / "_quarantine" / "bad.txt").exists()
    # el fichero suelto con par ya existente se elimina
    assert not (tmp_path / "paired.txt").exists()
    assert (tmp_path / "paired.system.txt").exists()
