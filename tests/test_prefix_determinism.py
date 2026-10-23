"""Determinismo del prefijo cacheable (F3): mismos datos -> mismos bytes.

El prefijo estable es tools -> system -> catálogo. Si dos construcciones con los
mismos datos no producen bytes idénticos, el proveedor no puede acertar la caché.
En el piloto el catálogo de delegación va dentro del system (``# DELEGATION``) y el
historial en el mensaje dinámico del usuario.
"""

import time
from pathlib import Path
from types import SimpleNamespace

from satellite.agents.prompting import build_meta_messages
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.runtime import LLMAgent, build_system_prompt, build_user_prompt
from satellite.core.context_models import ContextBundle
from satellite.core.spec import AgentSpec
from satellite.core.task import Task
from satellite.core.tool_models import ToolSchema
from satellite.orchestration.pilot import PilotOrchestrator
from satellite.orchestration.turn_context import render_history, with_conversation
from satellite.providers.fake import FakeLLM
from satellite.providers.prefix import (
    canonical_fingerprint,
    canonical_json,
    fingerprint,
    prefix_hash,
    prefix_payload,
    tools_canonical,
)


class FakeTool:
    """Tool mínima para ``build_system_prompt`` (sólo usa id/description)."""

    def __init__(self, tool_id: str, description: str = "") -> None:
        self.id = tool_id
        self.description = description


def make_spec(spec_id: str, *, capabilities=("do_thing",), tools=("read_file",), **overrides) -> AgentSpec:
    data = dict(
        id=spec_id,
        name=spec_id.replace("_", " ").title(),
        description=f"hace {spec_id}",
        capabilities=capabilities,
        prompt_ref=spec_id,
        tools=tools,
    )
    data.update(overrides)
    return AgentSpec(**data)


PROMPTS = Path(__file__).resolve().parent.parent / "satellite" / "agents" / "prompts"


class PilotFactory:
    """Crea el piloto real (prompts del paquete) sin tools ni proveedor de verdad."""

    def __init__(self) -> None:
        self.prompts = DefaultPromptRegistry()
        self.prompts.load_dir(PROMPTS)

    def create(self, spec_id, *, model_role=None):
        spec = AgentSpec(id=spec_id, name="Pilot", capabilities=("converse",), prompt_ref=spec_id)
        return LLMAgent(
            spec=spec,
            system_prompt=self.prompts.get_system(spec_id),
            user_template=self.prompts.get_user(spec_id),
            tools=[],
            provider=FakeLLM(),
        )


def pilot_system(specs) -> str:
    """System del piloto tal como lo arma ``PilotOrchestrator`` con ese catálogo."""
    registry = DefaultAgentRegistry()
    for spec in specs:
        registry.register_spec(spec)
    services = SimpleNamespace(registry=registry)
    agent = PilotOrchestrator(services)._pilot(PilotFactory(), services)
    return build_system_prompt(
        agent.system_prompt,
        output_schema=agent.spec.output_schema,
        tools=agent.tools,
        can_call=agent.spec.can_call,
    )


# ---------------------------------------------------------------- serialización


def test_canonical_json_sorts_keys_and_compacts() -> None:
    assert canonical_json({"b": 1, "a": {"d": 2, "c": 3}}) == '{"a":{"c":3,"d":2},"b":1}'


def test_tools_canonical_sorts_by_name() -> None:
    schemas = [ToolSchema(name="zeta"), ToolSchema(name="alpha"), ToolSchema(name="mu")]
    assert [tool["name"] for tool in tools_canonical(schemas)] == ["alpha", "mu", "zeta"]


def test_prefix_is_byte_identical_across_calls() -> None:
    tools = [ToolSchema(name="read_file", description="lee", parameters={"type": "object"})]
    first = prefix_payload(tools, "SYSTEM", "CATALOG")
    second = prefix_payload(tools, "SYSTEM", "CATALOG")
    assert first == second
    assert prefix_hash(tools, "SYSTEM", "CATALOG") == prefix_hash(tools, "SYSTEM", "CATALOG")


def test_prefix_hash_ignores_tool_and_key_order() -> None:
    one = [
        ToolSchema(name="b", parameters={"x": 1, "y": 2}),
        ToolSchema(name="a"),
    ]
    two = [
        ToolSchema(name="a"),
        ToolSchema(name="b", parameters={"y": 2, "x": 1}),
    ]
    assert prefix_hash(one, "SYS") == prefix_hash(two, "SYS")


def test_prefix_hash_changes_with_content() -> None:
    tools = [ToolSchema(name="a")]
    assert prefix_hash(tools, "SYS-1") != prefix_hash(tools, "SYS-2")
    assert prefix_hash(tools, "SYS", "C1") != prefix_hash(tools, "SYS", "C2")


def test_fingerprints_are_stable() -> None:
    assert fingerprint("hola") == fingerprint("hola")
    assert fingerprint("hola") != fingerprint("hola!")
    assert canonical_fingerprint({"a": 1, "b": 2}) == canonical_fingerprint({"b": 2, "a": 1})
    assert len(fingerprint("x")) == 64


def test_spec_fingerprint_ignores_dict_key_order() -> None:
    left = make_spec("alpha_agent", output_schema={"type": "object", "properties": {"a": {}, "b": {}}})
    right = make_spec("alpha_agent", output_schema={"properties": {"b": {}, "a": {}}, "type": "object"})
    assert canonical_fingerprint(left.to_dict()) == canonical_fingerprint(right.to_dict())


# ---------------------------------------------------------------- catálogo


def test_pilot_catalog_orders_specs_by_id() -> None:
    specs = [make_spec("zeta_agent"), make_spec("alpha_agent"), make_spec("mu_agent")]
    text = pilot_system(specs)
    assert text == pilot_system(list(reversed(specs)))
    positions = [text.index(f"- {spec_id}:") for spec_id in ("alpha_agent", "mu_agent", "zeta_agent")]
    assert positions == sorted(positions)
    # el piloto no se ofrece a sí mismo como destino
    assert "- pilot:" not in pilot_system(specs + [make_spec("pilot")])


def test_render_history_keeps_chronological_order() -> None:
    history = [{"role": "user", "content": "uno"}, {"role": "assistant", "content": "dos"}]
    text = render_history(history)
    assert text.index("uno") < text.index("dos")
    # el orden del historial SÍ importa (es cronológico)
    assert text != render_history(list(reversed(history)))


def test_render_history_empty_and_sanitized() -> None:
    assert render_history(None) == ""
    assert render_history([{"role": "user", "content": "   "}]) == ""
    assert render_history([{"role": "user", "content": "hola"}]).startswith("Recent conversation")


def test_history_is_not_in_the_stable_pilot_system(tmp_path) -> None:
    specs = [make_spec("alpha_agent")]
    system = pilot_system(specs)
    task = Task(
        goal="x",
        project_root=tmp_path,
        metadata={"history": [{"role": "user", "content": "TAREA-ANTERIOR"}]},
    )
    user = build_user_prompt(
        PilotFactory().prompts.get_user("pilot"),
        task={"goal": task.goal},
        context=with_conversation(ContextBundle(), task),
    )
    assert "TAREA-ANTERIOR" not in system
    assert "TAREA-ANTERIOR" in user
    # y el system es el mismo con o sin historial -> prefijo estable
    assert system == pilot_system(specs)


def test_pilot_without_catalog_is_stable() -> None:
    assert pilot_system([]) == pilot_system([])
    assert "# DELEGATION" not in pilot_system([])


# ---------------------------------------------------------------- system prompt


def test_system_prompt_is_stable_for_schema_key_order() -> None:
    template = "Eres un escritor.\nSALIDA:\n{output_schema}"
    left = build_system_prompt(template, output_schema={"type": "object", "properties": {"b": 1, "a": 2}})
    right = build_system_prompt(template, output_schema={"properties": {"a": 2, "b": 1}, "type": "object"})
    assert left == right
    # se ordenan las claves pero se mantiene el formato legible
    assert '"a": 2' in left


def test_system_prompt_is_stable_for_tool_order() -> None:
    tools_one = [FakeTool("write_file", "escribe"), FakeTool("read_file", "lee")]
    tools_two = [FakeTool("read_file", "lee"), FakeTool("write_file", "escribe")]
    template = "TOOLS:\n{tools}"
    assert build_system_prompt(template, tools=tools_one) == build_system_prompt(
        template, tools=tools_two
    )
    assert build_system_prompt(template, tools=tools_one).index("read_file") < build_system_prompt(
        template, tools=tools_one
    ).index("write_file")


# ---------------------------------------------------------------- sin volátiles


def test_prefix_has_no_volatile_data_across_calls() -> None:
    specs = [make_spec("alpha_agent"), make_spec("beta_agent")]
    system_prompt = "Sé útil.\n{output_schema}"
    user_template = "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}"

    def build() -> tuple[str, str, str]:
        messages, stable = build_meta_messages(
            system_prompt=system_prompt,
            user_template=user_template,
            output_schema={"type": "object"},
            catalog="\n".join(f"- id={spec.id}" for spec in specs),
            task="haz algo",
        )
        assert stable == 1
        return messages[0].content, messages[1].content, messages[2].content

    first = build()
    pilot_first = pilot_system(specs)
    time.sleep(0.01)  # un timestamp/UUID/contador rompería la igualdad
    second = build()
    assert first == second
    assert prefix_hash(None, first[0], first[1]) == prefix_hash(None, second[0], second[1])
    assert pilot_first == pilot_system(specs)


def test_user_prompt_is_deterministic() -> None:
    template = "{task}\n{context}\n{feedback}"
    bundle = ContextBundle(sections=())
    one = build_user_prompt(template, task={"goal": "x", "b": 2, "a": 1}, context=bundle, feedback=())
    two = build_user_prompt(template, task={"a": 1, "b": 2, "goal": "x"}, context=bundle, feedback=())
    assert one == two
    assert '"a": 1' in one
    assert "no feedback" in one
