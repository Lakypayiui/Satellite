"""Determinismo del prefijo cacheable (F3): mismos datos -> mismos bytes.

El prefijo estable es tools -> system -> catálogo. Si dos construcciones con los
mismos datos no producen bytes idénticos, el proveedor no puede acertar la caché.
"""

import time

from satellite.agents.prompting import build_meta_messages
from satellite.agents.runtime import build_system_prompt, build_user_prompt
from satellite.core.context_models import ContextBundle, ContextRef, ContextRefKind
from satellite.core.spec import AgentSpec
from satellite.core.tool_models import ToolSchema
from satellite.orchestration.planner_prompt import build_catalog_context, render_history
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


def test_catalog_context_orders_specs_by_id() -> None:
    specs = [make_spec("zeta_agent"), make_spec("alpha_agent"), make_spec("mu_agent")]
    text = build_catalog_context(specs)
    reversed_text = build_catalog_context(list(reversed(specs)))
    assert text == reversed_text
    positions = [text.index(f"id={spec_id}") for spec_id in ("alpha_agent", "mu_agent", "zeta_agent")]
    assert positions == sorted(positions)


def test_catalog_context_orders_hints() -> None:
    first = ContextRef(ContextRefKind.PATH, "a.txt")
    second = ContextRef(ContextRefKind.PATH, "b.txt")
    assert build_catalog_context([], (first, second)) == build_catalog_context([], (second, first))
    assert build_catalog_context([], (first, second)).index("a.txt") < build_catalog_context(
        [], (first, second)
    ).index("b.txt")


def test_render_history_keeps_chronological_order() -> None:
    history = [{"role": "user", "content": "uno"}, {"role": "assistant", "content": "dos"}]
    text = render_history(history)
    assert text.index("uno") < text.index("dos")
    # el orden del historial SÍ importa (es cronológico)
    assert text != render_history(list(reversed(history)))


def test_render_history_empty_and_sanitized() -> None:
    assert render_history(None) == ""
    assert render_history([{"role": "user", "content": "   "}]) == ""
    assert render_history([{"role": "user", "content": "hola"}]).startswith("Conversación reciente")


def test_history_is_not_in_the_stable_catalog_block() -> None:
    specs = [make_spec("alpha_agent")]
    history = [{"role": "user", "content": "TAREA-ANTERIOR"}]
    catalog = build_catalog_context(specs)
    assert "TAREA-ANTERIOR" not in catalog
    # y el catálogo es el mismo con o sin historial -> breakpoint (b) estable
    assert catalog == build_catalog_context(specs, ())


def test_catalog_context_empty_is_stable() -> None:
    assert build_catalog_context([]) == build_catalog_context([], ())
    assert "(catálogo vacío)" in build_catalog_context([])


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
            catalog=build_catalog_context(specs),
            task="haz algo",
        )
        assert stable == 1
        return messages[0].content, messages[1].content, messages[2].content

    first = build()
    time.sleep(0.01)  # un timestamp/UUID/contador rompería la igualdad
    second = build()
    assert first == second
    assert prefix_hash(None, first[0], first[1]) == prefix_hash(None, second[0], second[1])


def test_user_prompt_is_deterministic() -> None:
    template = "{task}\n{context}\n{feedback}"
    bundle = ContextBundle(sections=())
    one = build_user_prompt(template, task={"goal": "x", "b": 2, "a": 1}, context=bundle, feedback=())
    two = build_user_prompt(template, task={"a": 1, "b": 2, "goal": "x"}, context=bundle, feedback=())
    assert one == two
    assert '"a": 1' in one
    assert "sin feedback" in one
