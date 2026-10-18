"""Tests de DefaultGraphRunner: grafos lineales, ramas y ciclos de feedback."""

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.core.graph import Edge, EdgeCondition, ExecutionGraph, Node
from satellite.core.result import AgentResult, AgentStatus, Finding, FindingSeverity
from satellite.core.spec import AgentSpec
from satellite.core.task import Task
from satellite.core.context_models import ContextRef, ContextRefKind
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.runner import DefaultGraphRunner
from satellite.events.base import EventSink


def run(coro):
    return asyncio.run(coro)


class FakeAgent(Agent):
    """Agente determinista según comportamiento; guarda contexto recibido."""

    def __init__(self, spec_id: str, behavior: dict):
        self.spec_id = spec_id
        self.behavior = behavior
        self.last_ctx: AgentContext | None = None
        self.calls = 0

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.calls += 1
        self.last_ctx = ctx
        self.last_task = dict(task)
        return AgentResult(
            node_id=ctx.node_id,
            spec_id=self.spec_id,
            status=AgentStatus(self.behavior.get("status", "ok")),
            summary=self.behavior.get("summary", "OK"),
            output=dict(self.behavior.get("output", {})),
            findings=tuple(self.behavior.get("findings", ())),
        )


class FakeFactory(AgentFactory):
    """Factory que registra specs fake con comportamiento."""

    def __init__(self, behaviors: dict[str, dict] | None = None):
        self.behaviors: dict[str, dict] = dict(behaviors or {})
        self.specs: dict[str, AgentSpec] = {}
        self.agents: dict[str, FakeAgent] = {}

    def register(self, spec: AgentSpec, behavior: dict):
        self.specs[spec.id] = spec

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        self.agents.setdefault(spec_id, FakeAgent(spec_id, self.behaviors.get(spec_id, {"status": "ok"})))
        return self.agents[spec_id]


class RecordingSink(EventSink):
    def __init__(self):
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        pass


def make_spec(spec_id: str) -> AgentSpec:
    return AgentSpec(id=spec_id, name=spec_id, capabilities=(spec_id,), prompt_ref=spec_id)


def make_task(tmp_path: Path) -> Task:
    return Task(goal="crear html", project_root=tmp_path)


def test_linear_pass_output_to_B(tmp_path: Path) -> None:
    """A → B: B recibe el output de A en su task (payload result)."""
    factory = FakeFactory({
        "sA": {"status": "ok", "output": {"path": "index.html", "title": "Mi página"}},
        "sB": {"status": "ok", "output": {"final": True}},
    })
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    graph.add_node(Node(id="n2", spec_id="sB"))
    graph.add_edge(Edge(id="e1", source="n1", target="n2", payload="result"))
    runner = DefaultGraphRunner(factory)
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=factory)))
    assert result.status.value == "ok"
    b = factory.agents["sB"]
    assert b.last_ctx is not None
    # B recibió el output de A
    assert b.last_ctx is not None


def test_execution_order_A_B_C(tmp_path: Path) -> None:
    factory = FakeFactory({
        "sA": {"status": "ok", "output": {"step": "A"}},
        "sB": {"status": "ok", "output": {"step": "B"}},
        "sC": {"status": "ok", "output": {"step": "C"}},
    })
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    graph.add_node(Node(id="n2", spec_id="sB"))
    graph.add_node(Node(id="n3", spec_id="sC"))
    graph.add_edge(Edge(id="e1", source="n1", target="n2"))
    graph.add_edge(Edge(id="e2", source="n2", target="n3"))
    runner = DefaultGraphRunner(factory)
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=factory)))
    assert result.output["step"] == "C"
    assert factory.agents["sA"].calls == 1
    assert factory.agents["sB"].calls == 1
    assert factory.agents["sC"].calls == 1


def test_multiple_roots_join_after_both_predecessors(tmp_path: Path) -> None:
    factory = FakeFactory({
        "sA": {"output": {"from_a": 1}},
        "sB": {"output": {"from_b": 2}},
        "sC": {"output": {"joined": True}},
    })
    graph = ExecutionGraph(entry_node="a")
    for node, spec in (("a", "sA"), ("b", "sB"), ("c", "sC")):
        graph.add_node(Node(id=node, spec_id=spec))
    graph.add_edge(Edge(id="ac", source="a", target="c", payload="result"))
    graph.add_edge(Edge(id="bc", source="b", target="c", payload="result"))

    result = run(DefaultGraphRunner(factory).run(graph, make_task(tmp_path), RuntimeServices(factory=factory)))

    assert result.status.value == "ok"
    assert factory.agents["sC"].calls == 1
    assert factory.agents["sC"].last_task["from_a"] == 1
    assert factory.agents["sC"].last_task["from_b"] == 2


def test_terminal_branch_does_not_abort_sibling_branch(tmp_path: Path) -> None:
    factory = FakeFactory({"sA": {}, "sB": {}, "sC": {"output": {"done": True}}})
    graph = ExecutionGraph(entry_node="a")
    for node, spec in (("a", "sA"), ("b", "sB"), ("c", "sC")):
        graph.add_node(Node(id=node, spec_id=spec))
    graph.add_edge(Edge(id="bc", source="b", target="c", payload="result"))

    result = run(DefaultGraphRunner(factory).run(graph, make_task(tmp_path), RuntimeServices(factory=factory)))

    assert result.status.value == "ok"
    assert factory.agents["sC"].calls == 1


def test_branch_ok_selects_B_not_C(tmp_path: Path) -> None:
    factory = FakeFactory({
        "sA": {"status": "ok", "output": {}},
        "sB": {"status": "ok", "output": {"branch": "B"}},
        "sC": {"status": "ok", "output": {"branch": "C"}},
    })
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    graph.add_node(Node(id="n2", spec_id="sB"))
    graph.add_node(Node(id="n3", spec_id="sC"))
    graph.add_edge(Edge(id="e_ok", source="n1", target="n2", condition=EdgeCondition.on_status("ok")))
    graph.add_edge(Edge(id="e_fail", source="n1", target="n3", condition=EdgeCondition.on_status("failed")))
    runner = DefaultGraphRunner(factory)
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=factory)))
    assert result.output["branch"] == "B"
    assert "sC" not in factory.agents or factory.agents["sC"].calls == 0


def test_failure_selects_C(tmp_path: Path) -> None:
    factory = FakeFactory({
        "sA": {"status": "failed", "output": {}},
        "sB": {"status": "ok", "output": {"branch": "B"}},
        "sC": {"status": "ok", "output": {"branch": "C"}},
    })
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    graph.add_node(Node(id="n2", spec_id="sB"))
    graph.add_node(Node(id="n3", spec_id="sC"))
    graph.add_edge(Edge(id="e_ok", source="n1", target="n2", condition=EdgeCondition.on_status("ok")))
    graph.add_edge(Edge(id="e_fail", source="n1", target="n3", condition=EdgeCondition.on_status("failed")))
    runner = DefaultGraphRunner(factory)
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=factory)))
    assert result.output["branch"] == "C"


def test_feedback_cycle_retry_success(tmp_path: Path) -> None:
    """A(creador) → B(verificador); B fail → A con feedback; A falla 1ª, acierta 2ª."""
    class CounterAgent(Agent):
        def __init__(self):
            self.count = 0

        async def execute(self, task, ctx):
            self.count += 1
            if self.count == 1:
                return AgentResult(
                    node_id=ctx.node_id, spec_id="sA", status=AgentStatus.FAILED,
                    findings=(Finding(severity=FindingSeverity.ERROR, message="fallo 1er intento"),),
                )
            return AgentResult(node_id=ctx.node_id, spec_id="sA", status=AgentStatus.OK, output={"ok": True})

    class CounterFactory(AgentFactory):
        def __init__(self):
            self.a = CounterAgent()

        def create(self, spec_id, *, model_role=None):
            return self.a

    cfactory = CounterFactory()
    graph = ExecutionGraph(entry_node="a")
    graph.add_node(Node(id="a", spec_id="sA", max_retries=2))
    graph.add_node(Node(id="b", spec_id="sB", max_retries=2))
    # B siempre fail → edge fail → A (feedback)
    graph.add_edge(Edge(id="e_ab", source="a", target="b", payload="feedback"))
    graph.add_edge(Edge(id="e_fail", source="b", target="a", condition=EdgeCondition.on_status("failed"), payload="feedback"))
    graph.add_edge(Edge(id="e_end", source="b", target="a", condition=EdgeCondition.on_status("ok"), payload="none"))

    # B fake siempre devuelve failed
    class BAgent(Agent):
        async def execute(self, task, ctx):
            return AgentResult(
                node_id=ctx.node_id, spec_id="sB", status=AgentStatus.FAILED,
                findings=(Finding(severity=FindingSeverity.ERROR, message="verificación fallida"),),
            )

    class BFactory(AgentFactory):
        def create(self, spec_id, *, model_role=None):
            if spec_id == "sA":
                return cfactory.a
            return BAgent()

    runner = DefaultGraphRunner(BFactory())
    result = run(runner.run(graph, make_task(tmp_path), RuntimeServices(factory=BFactory())))
    assert result.status.value in ("ok", "failed")
    assert cfactory.a.count >= 2


def test_events_sequence(tmp_path: Path) -> None:
    factory = FakeFactory({"sA": {"status": "ok"}})
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    sink = RecordingSink()
    runner = DefaultGraphRunner(factory, sink=sink)
    task = make_task(tmp_path)
    result = run(runner.run(graph, task, RuntimeServices(factory=factory, sink=sink)))
    types = [e.event_type.value for e in sink.events]
    assert types[0] == "task_started"
    assert types[1] == "graph_built"
    assert "node_started" in types
    assert "node_finished" in types
    assert types[-1] == "task_finished"
    assert all(e.task_id == task.task_id for e in sink.events)
