"""Enrutado en el orquestador: los diagramas se delegan al agente de diagrama.

Comprueba las dos caras del enrutado condicional —una petición de diagrama se
atiende por ``DiagramFlow`` (que delega en ``pele_diagram_agent``) y el trabajo no
relacionado no— y el respaldo cuando el renderer no está disponible: la tarea sigue
completándose (no falla) y se devuelve la fuente Mermaid.
"""

import asyncio
import json
from pathlib import Path

from satellite.agents.factory import DefaultAgentFactory
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import (
    GraphRunner,
    Planner,
    PlanningResult,
    RuntimeServices,
)
from satellite.orchestration.diagram_flow import DiagramFlow
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools import diagram as diagram_module
from satellite.tools.diagram import RenderDiagramTool, RenderOutcome
from satellite.tools.registry import DefaultToolRegistry

PKG = Path(__file__).resolve().parent.parent / "satellite" / "agents"
SPECS = PKG / "specs"
PROMPTS = PKG / "prompts"


def run(coro):
    return asyncio.run(coro)


class RecordingPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        return PlanningResult(kind="needs_clarification", question="¿qué quieres exactamente?")


class StubRunner(GraphRunner):
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


class SpyDiagramFlow:
    """Flujo espía que registra si se le llamó, sin renderizar nada."""

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, task) -> TaskResult | None:
        self.runs += 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="diagrama")


def _tool_registry() -> DefaultToolRegistry:
    registry = DefaultToolRegistry()
    registry.register(RenderDiagramTool())
    return registry


def _factory(provider, project_root: Path) -> DefaultAgentFactory:
    registry = DefaultAgentRegistry()
    registry.load_dir(SPECS)
    prompts = DefaultPromptRegistry()
    prompts.load_dir(PROMPTS)
    return DefaultAgentFactory(
        registry=registry,
        prompts=prompts,
        tools=_tool_registry(),
        provider=provider,
        policy=DefaultSecurityPolicy(allowed_tools={"render_diagram", "request_context"}),
        project_root=project_root,
    )


def _orchestrator(planner: Planner, runner: GraphRunner, flow) -> DefaultOrchestrator:
    orch = DefaultOrchestrator(
        planner, runner, RuntimeServices(registry=None, tools=_tool_registry())
    )
    orch.diagram_flow = flow
    return orch


def test_diagram_request_routes_to_flow(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out" / "diagram.svg"

    def fake_render(source, *, project_root=None, out_path=None, **kwargs):
        Path(out_path).write_text("<svg>diagram</svg>", encoding="utf-8")
        return RenderOutcome(ok=True, svg="<svg>diagram</svg>", diagram_type="flowchart")

    monkeypatch.setattr(diagram_module, "render_mermaid", fake_render)
    provider = FakeLLM(
        script=[
            {
                "tool_calls": [
                    {
                        "id": "1",
                        "name": "render_diagram",
                        "arguments": {"diagram": "flowchart LR\n A --> B", "out_path": "out/diagram.svg"},
                    }
                ]
            },
            {
                "content": json.dumps(
                    {
                        "mermaid": "flowchart LR\n A --> B",
                        "rendered": True,
                        "svg_path": str(out),
                        "diagram_type": "flowchart",
                    }
                )
            },
        ]
    )
    flow = DiagramFlow(factory=_factory(provider, tmp_path), project_root=tmp_path, out_path="out/diagram.svg")
    planner = RecordingPlanner()
    orch = _orchestrator(planner, StubRunner(), flow)

    task = Task(goal="dibuja un diagrama de flujo del login", project_root=tmp_path)
    result = run(orch.execute(task))

    assert planner.calls == 0  # no pasa por el planificador
    assert result.status is TaskStatus.OK
    assert result.output["mode"] == "diagram"
    assert result.output["rendered"] is True
    assert Path(result.output["svg_path"]).is_file()


def test_unrelated_task_does_not_route(tmp_path: Path) -> None:
    planner = RecordingPlanner()
    flow = SpyDiagramFlow()
    orch = _orchestrator(planner, StubRunner(), flow)

    task = Task(goal="arregla el bug del login", project_root=tmp_path)
    result = run(orch.execute(task))

    assert flow.runs == 0
    assert planner.calls == 1
    assert result.output["mode"] == "needs_clarification"


def test_pele_unavailable_keeps_normal_flow(tmp_path: Path, monkeypatch) -> None:
    def fake_render(source, *, project_root=None, out_path=None, **kwargs):
        return RenderOutcome(ok=False, error_code="pele_unavailable", error="no hay pele")

    monkeypatch.setattr(diagram_module, "render_mermaid", fake_render)
    source = "flowchart LR\n A --> B"
    provider = FakeLLM(
        script=[
            {
                "tool_calls": [
                    {"id": "1", "name": "render_diagram", "arguments": {"diagram": source}}
                ]
            },
            {"content": json.dumps({"mermaid": source, "rendered": False})},
        ]
    )
    flow = DiagramFlow(factory=_factory(provider, tmp_path), project_root=tmp_path)
    planner = RecordingPlanner()
    orch = _orchestrator(planner, StubRunner(), flow)

    goal = "renderiza este diagrama\n```mermaid\nflowchart LR\n A --> B\n```"
    result = run(orch.execute(Task(goal=goal, project_root=tmp_path)))

    assert planner.calls == 0
    assert result.status is TaskStatus.OK  # el flujo normal se preserva
    assert result.output["mode"] == "diagram"
    assert result.output["rendered"] is False
    assert "flowchart LR" in result.output["mermaid"]
