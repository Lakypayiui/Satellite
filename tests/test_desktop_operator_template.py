"""``desktop_operator`` es un agente base, no una plantilla creable.

La plantilla antigua (prompt estático + ``create_template``) se sustituyó por el
spec/prompt de ``satellite/agents`` para que el orquestador lo tenga siempre en el
catálogo de superficies con humano.
"""

from pathlib import Path

from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.templates import get_template, has_template, template_ids

PKG = Path(__file__).resolve().parent.parent / "satellite" / "agents"


def test_desktop_operator_is_not_a_template() -> None:
    assert has_template("desktop_operator") is False
    assert get_template("desktop_operator") is None
    assert "desktop_operator" not in template_ids()


def test_desktop_operator_is_a_shipped_base_agent() -> None:
    registry = DefaultAgentRegistry()
    registry.load_dir(PKG / "specs")
    spec = registry.get("desktop_operator")
    assert spec.prompt_ref == "desktop_operator"
    assert spec.tools == (
        "desktop_list_windows",
        "desktop_observe",
        "desktop_act",
        "run_command",
    )
    assert spec.output_schema is not None
    assert (PKG / "prompts" / "desktop_operator.system.txt").is_file()
    assert (PKG / "prompts" / "desktop_operator.user.txt").is_file()
