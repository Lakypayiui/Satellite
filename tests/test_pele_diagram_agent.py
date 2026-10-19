"""Agente declarativo ``pele_diagram_agent``: spec, prompts, fábrica y registro.

El agente de diagrama es una capacidad **condicional** que se enruta desde
``DiagramFlow``. Aquí se comprueba que el spec es declarativo y completo, que sus
prompts siguen el contrato system/user, que la fábrica lo construye con la tool de
render, y que se registra en el runtime/paquete a través del mismo mecanismo
(``verify_and_restore`` + catálogo) que el resto de agentes base.
"""

import asyncio
from pathlib import Path

from satellite.agents.factory import DefaultAgentFactory
from satellite.agents.prompts import DefaultPromptRegistry, find_placeholders
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.store import AgentCatalogStore
from satellite.core.task import Task
from satellite.kernel.manifest import RESERVED_AGENT_IDS, is_reserved, verify_and_restore
from satellite.orchestration.diagram_flow import DiagramFlow
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.diagram import RenderDiagramTool
from satellite.tools.registry import DefaultToolRegistry

PKG = Path(__file__).resolve().parent.parent / "satellite" / "agents"
SPECS = PKG / "specs"
PROMPTS = PKG / "prompts"

AGENT_ID = "pele_diagram_agent"


def run(coro):
    return asyncio.run(coro)


def _registry() -> DefaultAgentRegistry:
    registry = DefaultAgentRegistry()
    registry.load_dir(SPECS)
    return registry


def _prompts() -> DefaultPromptRegistry:
    prompts = DefaultPromptRegistry()
    prompts.load_dir(PROMPTS)
    return prompts


def _factory(project_root: Path, provider=None) -> DefaultAgentFactory:
    tools = DefaultToolRegistry()
    tools.register(RenderDiagramTool())
    return DefaultAgentFactory(
        registry=_registry(),
        prompts=_prompts(),
        tools=tools,
        provider=provider or FakeLLM(),
        policy=DefaultSecurityPolicy(allowed_tools={"render_diagram", "request_context"}),
        project_root=project_root,
    )


# ---------------------------------------------------------------- spec declarativo


def test_spec_is_shipped_and_shaped() -> None:
    spec = _registry().get(AGENT_ID)
    assert spec.id == AGENT_ID
    assert spec.prompt_ref == AGENT_ID
    assert spec.tools == ("render_diagram",)
    assert "render_mermaid" in spec.capabilities
    assert "architecture_diagram" in spec.capabilities
    assert spec.model_role == "agent"
    assert spec.timeout_s > 0


def test_output_schema_is_runtime_compatible() -> None:
    schema = _registry().get(AGENT_ID).output_schema
    assert isinstance(schema, dict)
    assert set(schema["required"]) == {"mermaid", "rendered"}
    assert set(schema["properties"]) == {
        "mermaid",
        "rendered",
        "svg_path",
        "diagram_type",
        "content",
    }
    assert schema["additionalProperties"] is False


def test_agent_id_is_kernel_reserved() -> None:
    assert AGENT_ID in RESERVED_AGENT_IDS
    assert is_reserved(AGENT_ID)


def test_agent_is_discoverable_by_capability() -> None:
    found = {spec.id for spec in _registry().find_by_capability("architecture diagram")}
    assert AGENT_ID in found


def test_prompts_follow_system_user_contract() -> None:
    prompts = _prompts()
    system = prompts.get_system(AGENT_ID)
    user = prompts.get_user(AGENT_ID)
    assert system.strip() and user.strip()
    # el system es estático: nada dinámico; sólo {tools}/{output_schema}
    assert not (set(find_placeholders(system)) & {"task", "context", "feedback"})
    assert set(find_placeholders(system)) <= {"tools", "output_schema"}
    assert "task" in find_placeholders(user)


# ---------------------------------------------------------------- fábrica / runtime


def test_factory_creates_agent_with_render_tool(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    assert factory.can_create(AGENT_ID) is True
    agent = factory.create(AGENT_ID)
    assert {tool.id for tool in agent.tools} == {"render_diagram", "request_context"}


def test_installed_package_registers_agent(tmp_path: Path) -> None:
    """Mismo camino que el runtime: sync del paquete + carga del catálogo."""
    data = tmp_path / "data"
    verify_and_restore(SPECS, data, patterns=("*.json",))
    verify_and_restore(PROMPTS, data, patterns=("*.txt",))
    store = AgentCatalogStore(data)
    specs = {spec.id: spec for spec in store.load_specs()}
    assert AGENT_ID in specs
    assert AGENT_ID in store.load_prompts()


def test_flow_without_factory_falls_through(tmp_path: Path) -> None:
    flow = DiagramFlow(factory=None, project_root=tmp_path)
    task = Task(goal="dibuja un diagrama del login", project_root=tmp_path)
    assert run(flow.run(task)) is None
