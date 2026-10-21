"""Tests del flujo de expansión: gap → creator → aprobación → registro (Fase 6)."""

import asyncio
import json
from pathlib import Path

from satellite.agents.creator import AgentProposalGenerator
from satellite.agents.expander import AgentExpander, ExpansionOutcome, agent_spec_from_proposal
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.validator import AgentProposalValidator
from satellite.approval.base import AutoApprovalPort, DenyAllApprovalPort
from satellite.approval.policy import ApprovalPolicy
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.plan import CapabilityGap
from satellite.core.proposal import ProposedAgent
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.events.bus import EventBus
from satellite.kernel.policy import PolicyMode
from satellite.orchestration.base import (
    GraphRunner,
    Planner,
    PlanningResult,
    RuntimeServices,
)
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.providers.fake import FakeLLM
from satellite.tools.fs import ReadFileTool
from satellite.tools.registry import DefaultToolRegistry


def run(coro):
    return asyncio.run(coro)


PROPOSAL = {
    "type": "proposed_agent",
    "id": "doc_summarizer",
    "name": "Doc Summarizer",
    "description": "resume documentos largos en puntos clave",
    "capabilities": ["summarize_docs", "extract_key_points"],
    "tools": ["read_file"],
    "validators": [],
    "model_role": "generated",
    "max_turns": 6,
    "timeout_s": 120,
    "system_prompt": "# ROL\nResume documentos.",
    "user_template": "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
    "example_tasks": [
        "resumir un informe de 40 páginas",
        "extraer los puntos clave de un README",
        "condensar las notas de una reunión",
    ],
    "rationale": "ningún agente resume texto",
    "reuse_checked": True,
}


def make_registry() -> DefaultAgentRegistry:
    reg = DefaultAgentRegistry()
    reg.register_spec(AgentSpec(id="orchestrator", name="O", capabilities=("plan_tasks",), prompt_ref="orchestrator"))
    reg.register_spec(AgentSpec(id="agent_creator", name="C", capabilities=("create_agent",), prompt_ref="agent_creator"))
    reg.register_spec(AgentSpec(id="skill_curator", name="K", capabilities=("curate_skills",), prompt_ref="skill_curator"))
    return reg


def make_expander(registry, script, *, policy_mode=PolicyMode.ASK, sink=None) -> AgentExpander:
    prompts = DefaultPromptRegistry()
    prompts.register_pair(
        "agent_creator",
        "N:{task}\nC:{context}\nF:{feedback}\nS:{output_schema}\nT:{tools}",
        "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
    )
    tools = DefaultToolRegistry()
    tools.register(ReadFileTool())
    validator = AgentProposalValidator(registry, tool_ids={"read_file"}, allowed_roles=("generated",))
    creator = AgentProposalGenerator(
        FakeLLM(script=script), registry, prompts, tools, validator=validator
    )
    return AgentExpander(
        creator, registry, prompts, policy=ApprovalPolicy(policy_mode), sink=sink
    )


class GapThenPlanPlanner(Planner):
    def __init__(self, agent_id: str = "doc_summarizer") -> None:
        self.calls = 0
        self.agent_id = agent_id

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        if self.calls == 1:
            return PlanningResult(
                kind="capability_gap",
                gap=CapabilityGap(missing="resumir documentos largos"),
            )
        graph = ExecutionGraph(entry_node="s1")
        graph.add_node(Node(id="s1", spec_id=self.agent_id, task={"goal": "x"}))
        return PlanningResult(kind="plan", graph=graph)


class OkRunner(GraphRunner):
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


def make_orchestrator(registry, expander, planner, runner, approval, sink=None):
    services = RuntimeServices(registry=registry, approval=approval, sink=sink)
    return DefaultOrchestrator(planner, runner, services, sink=sink, expander=expander)


def make_task(tmp_path: Path) -> Task:
    return Task(goal="resumir documentos largos", project_root=tmp_path)


class RecordingApproval(AutoApprovalPort):
    """Aprueba, pero deja constancia de que se preguntó al usuario."""

    def __init__(self) -> None:
        self.calls = 0

    async def request(self, proposal, ctx):
        self.calls += 1
        return await super().request(proposal, ctx)


def test_gap_triggers_creation_and_replan(tmp_path: Path) -> None:
    registry = make_registry()
    expander = make_expander(registry, [{"content": json.dumps(PROPOSAL)}])
    planner = GapThenPlanPlanner()
    runner = OkRunner()
    approval = RecordingApproval()
    orch = make_orchestrator(registry, expander, planner, runner, approval)
    result = run(orch.execute(make_task(tmp_path)))
    assert result.status is TaskStatus.OK
    assert registry.get("doc_summarizer").id == "doc_summarizer"
    assert planner.calls == 2
    assert runner.runs == 1
    # ASK: la creación pasó por el puerto de aprobación del usuario.
    assert approval.calls == 1


def test_rejected_approval_keeps_gap(tmp_path: Path) -> None:
    registry = make_registry()
    expander = make_expander(registry, [{"content": json.dumps(PROPOSAL)}])
    planner = GapThenPlanPlanner()
    runner = OkRunner()
    orch = make_orchestrator(registry, expander, planner, runner, DenyAllApprovalPort())
    result = run(orch.execute(make_task(tmp_path)))
    assert result.status is TaskStatus.NEEDS_USER_INPUT
    assert result.output["mode"] == "capability_gap"
    assert "doc_summarizer" not in {s.id for s in registry.all()}
    assert planner.calls == 1
    assert runner.runs == 0


def test_no_expander_returns_gap(tmp_path: Path) -> None:
    registry = make_registry()
    planner = GapThenPlanPlanner()
    orch = make_orchestrator(registry, None, planner, OkRunner(), AutoApprovalPort())
    result = run(orch.execute(make_task(tmp_path)))
    assert result.status is TaskStatus.NEEDS_USER_INPUT
    assert result.output["mode"] == "capability_gap"


def test_reuse_outcome_replans(tmp_path: Path) -> None:
    registry = make_registry()
    expander = make_expander(
        registry, [{"content": json.dumps({"type": "reuse", "agent_id": "orchestrator"})}]
    )
    planner = GapThenPlanPlanner(agent_id="orchestrator")
    runner = OkRunner()
    orch = make_orchestrator(registry, expander, planner, runner, AutoApprovalPort())
    result = run(orch.execute(make_task(tmp_path)))
    assert result.status is TaskStatus.OK
    assert planner.calls == 2


class FailOnceThenAvailablePlanner(Planner):
    """Falla, replanifica pidiendo un agente ausente y luego ya existe."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        agent_id = "orchestrator" if self.calls == 1 else "doc_summarizer"
        graph = ExecutionGraph(entry_node="s1")
        graph.add_node(Node(id="s1", spec_id=agent_id, task={"goal": "x"}))
        return PlanningResult(kind="plan", graph=graph)


class FailOnceRunner(GraphRunner):
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        status = TaskStatus.FAILED if self.runs == 1 else TaskStatus.OK
        return TaskResult(task_id=task.task_id, status=status, summary="ok")


def test_missing_agent_in_replan_also_triggers_creation(tmp_path: Path) -> None:
    registry = make_registry()
    expander = make_expander(registry, [{"content": json.dumps(PROPOSAL)}])
    planner = FailOnceThenAvailablePlanner()
    runner = FailOnceRunner()
    orch = make_orchestrator(
        registry, expander, planner, runner, AutoApprovalPort()
    )

    result = run(orch.execute(make_task(tmp_path)))

    assert result.status is TaskStatus.OK
    assert planner.calls == 3
    assert runner.runs == 2
    assert registry.get("doc_summarizer").id == "doc_summarizer"


class FailOnceThenGapPlanner(Planner):
    """El replan pide una capacidad nueva en vez de un grafo."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        if self.calls == 1:
            graph = ExecutionGraph(entry_node="s1")
            graph.add_node(Node(id="s1", spec_id="orchestrator", task={"goal": "x"}))
            return PlanningResult(kind="plan", graph=graph)
        if self.calls == 2:
            return PlanningResult(
                kind="capability_gap",
                gap=CapabilityGap(missing="resumir documentos largos"),
            )
        graph = ExecutionGraph(entry_node="s1")
        graph.add_node(Node(id="s1", spec_id="doc_summarizer", task={"goal": "x"}))
        return PlanningResult(kind="plan", graph=graph)


def test_capability_gap_in_replan_also_triggers_creation(tmp_path: Path) -> None:
    registry = make_registry()
    expander = make_expander(registry, [{"content": json.dumps(PROPOSAL)}])
    planner = FailOnceThenGapPlanner()
    runner = FailOnceRunner()
    orch = make_orchestrator(
        registry, expander, planner, runner, AutoApprovalPort()
    )

    result = run(orch.execute(make_task(tmp_path)))

    assert result.status is TaskStatus.OK
    assert planner.calls == 3
    assert runner.runs == 2
    assert registry.get("doc_summarizer").id == "doc_summarizer"


def test_creator_error_returns_gap(tmp_path: Path) -> None:
    registry = make_registry()
    expander = make_expander(registry, [{"content": "nada"}, {"content": "nada"}])
    planner = GapThenPlanPlanner()
    orch = make_orchestrator(registry, expander, planner, OkRunner(), AutoApprovalPort())
    result = run(orch.execute(make_task(tmp_path)))
    assert result.status is TaskStatus.NEEDS_USER_INPUT


def test_expansion_emits_events(tmp_path: Path) -> None:
    registry = make_registry()
    bus = EventBus()
    seen: list = []
    bus.subscribe(seen.append)
    expander = make_expander(registry, [{"content": json.dumps(PROPOSAL)}], sink=bus)
    orch = make_orchestrator(registry, expander, GapThenPlanPlanner(), OkRunner(), AutoApprovalPort(), sink=bus)
    run(orch.execute(make_task(tmp_path)))
    types = [e.event_type.value for e in seen]
    assert "agent_proposed" in types
    assert "agent_approved" in types
    assert "agent_created" in types


def test_agent_spec_from_proposal() -> None:
    proposal = ProposedAgent.from_dict(PROPOSAL)
    spec = agent_spec_from_proposal(proposal)
    assert spec.id == "doc_summarizer"
    assert spec.prompt_ref == "doc_summarizer"
    assert spec.capabilities == ("summarize_docs", "extract_key_points")
    assert spec.tools == ("read_file",)


class StubExpander:
    """Expander falso que devuelve un resultado fijo."""

    def __init__(self, kind: str, reason: str = "") -> None:
        self.kind = kind
        self.reason = reason

    async def expand(self, need, *, task_id="", approval_port=None, extra_context=""):
        return ExpansionOutcome(kind=self.kind, reason=self.reason)


def test_skill_suggestion_surfaces_clarification(tmp_path: Path) -> None:
    registry = make_registry()
    expander = StubExpander("skill_suggested", "skill 'csv_to_json': convierte csv a json")
    orch = make_orchestrator(registry, expander, GapThenPlanPlanner(), OkRunner(), AutoApprovalPort())
    result = run(orch.execute(make_task(tmp_path)))
    assert result.output["mode"] == "needs_clarification"
    assert "skill" in result.summary.lower()
    assert planner_calls(orch) == 1


def planner_calls(orch) -> int:
    return getattr(orch.planner, "calls", 0)
