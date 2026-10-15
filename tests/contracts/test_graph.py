"""Tests de contrato del ExecutionGraph."""

import pytest

from satellite.core.graph import (
    Edge,
    EdgeCondition,
    ExecutionGraph,
    GraphLimits,
    Node,
)
from satellite.core.errors import GraphValidationError
from satellite.core.result import AgentResult, AgentStatus


def make_result(status: AgentStatus = AgentStatus.OK, **overrides) -> AgentResult:
    base = dict(node_id="n1", spec_id="html_writer", status=status)
    base.update(overrides)
    return AgentResult(**base)


def test_linear_ok_selects_edge() -> None:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="html_writer"))
    graph.add_node(Node(id="n2", spec_id="html_writer"))
    graph.add_edge(Edge(id="e1", source="n1", target="n2", condition=EdgeCondition.on_status("ok")))
    succ = graph.successors("n1", make_result(AgentStatus.OK))
    assert len(succ) == 1
    assert succ[0].id == "e1"
    assert graph.successors("n1", make_result(AgentStatus.FAILED)) == []


def test_failed_condition_selects_fail_edge() -> None:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="html_writer"))
    graph.add_node(Node(id="n2", spec_id="html_writer"))
    graph.add_node(Node(id="n3", spec_id="html_writer"))
    ok_edge = Edge(id="e_ok", source="n1", target="n2", condition=EdgeCondition.on_status("ok"))
    fail_edge = Edge(id="e_fail", source="n1", target="n3", condition=EdgeCondition.on_status("failed"))
    graph.add_edge(ok_edge)
    graph.add_edge(fail_edge)
    assert graph.successors("n1", make_result(AgentStatus.FAILED)) == [fail_edge]
    assert graph.successors("n1", make_result(AgentStatus.OK)) == [ok_edge]


def test_on_field_eq() -> None:
    cond = EdgeCondition.on_field("output.tests_passed", "eq", True)
    assert cond.matches(make_result(output={"tests_passed": True}))
    assert not cond.matches(make_result(output={"tests_passed": False}))
    assert not cond.matches(make_result(output={}))


def test_on_field_numeric_operators() -> None:
    result = make_result(output={"score": 5})
    assert EdgeCondition.on_field("output.score", "gt", 4).matches(result)
    assert not EdgeCondition.on_field("output.score", "gt", 5).matches(result)
    assert EdgeCondition.on_field("output.score", "ge", 5).matches(result)
    assert not EdgeCondition.on_field("output.score", "lt", 5).matches(result)
    assert EdgeCondition.on_field("output.score", "le", 5).matches(result)
    assert not EdgeCondition.on_field("output.score", "lt", 4).matches(result)


def test_on_field_missing_no_match() -> None:
    cond = EdgeCondition.on_field("output.inexistente", "eq", True)
    assert not cond.matches(make_result(output={}))


def test_validate_passes_linear() -> None:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="html_writer"))
    graph.add_node(Node(id="n2", spec_id="html_writer"))
    graph.add_edge(Edge(id="e1", source="n1", target="n2"))
    graph.validate()  # no lanza


def test_validate_passes_y_shape() -> None:
    graph = ExecutionGraph(entry_node="root")
    graph.add_node(Node(id="root", spec_id="s"))
    graph.add_node(Node(id="a", spec_id="s"))
    graph.add_node(Node(id="b", spec_id="s"))
    graph.add_edge(Edge(id="e1", source="root", target="a"))
    graph.add_edge(Edge(id="e2", source="root", target="b"))
    graph.validate()  # no lanza


def test_validate_entry_missing_raises() -> None:
    graph = ExecutionGraph(entry_node="nope")
    graph.add_node(Node(id="n1", spec_id="s"))
    with pytest.raises(GraphValidationError):
        graph.validate()


def test_validate_edge_target_missing_raises() -> None:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="s"))
    graph.add_edge(Edge(id="e1", source="n1", target="ghost"))
    with pytest.raises(GraphValidationError):
        graph.validate()


def test_validate_unreachable_node_raises() -> None:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="s"))
    graph.add_node(Node(id="n2", spec_id="s"))
    graph.add_edge(Edge(id="e1", source="n1", target="n2"))
    graph.add_node(Node(id="n3", spec_id="s"))  # sin arista desde n1/n2
    with pytest.raises(GraphValidationError):
        graph.validate()


def test_cycle_allowed_but_detected() -> None:
    graph = ExecutionGraph(entry_node="a")
    graph.add_node(Node(id="a", spec_id="s"))
    graph.add_node(Node(id="b", spec_id="s"))
    graph.add_edge(Edge(id="e1", source="a", target="b"))
    graph.add_edge(Edge(id="e2", source="b", target="a"))
    assert graph.has_cycles() is True
    graph.validate()  # no lanza: los ciclos se acotan por límites


def test_round_trip_graph() -> None:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(
        Node(id="n1", spec_id="html_writer", task={"goal": "crear index.html"},
             context_refs=(), max_retries=3)
    )
    graph.add_node(Node(id="n2", spec_id="verifier"))
    graph.add_edge(
        Edge(id="e1", source="n1", target="n2",
             condition=EdgeCondition.on_field("output.tests_passed", "eq", True),
             payload="result", mapping={"goal": "summary"})
    )
    graph.limits = GraphLimits(max_total_steps=50, max_node_visits=5)
    restored = ExecutionGraph.from_dict(graph.to_dict())
    assert restored.entry_node == graph.entry_node
    assert restored.nodes == graph.nodes
    assert restored.edges == graph.edges
    assert restored.limits == graph.limits
    assert restored.limits.max_total_steps == 50
