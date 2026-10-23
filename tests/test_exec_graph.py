"""Fase 0 del grafo de ejecución (docs/PLAN-grafo-piloto.md): el modelo y su validación.

Sin ejecutor todavía: aquí sólo se construye, se valida (ciclos, dependencias, ids,
límites) y se (de)serializa, y se comprueba la lógica de «qué nodos están listos».
"""

from __future__ import annotations

import pytest

from satellite.orchestration.graph import (
    ExecutionGraph,
    GraphError,
    GraphLimits,
    GraphNode,
)


def _node(node_id: str, *deps: str, **kw) -> GraphNode:
    return GraphNode(id=node_id, agent_id=kw.pop("agent_id", "python_coder"),
                     depends_on=tuple(deps), **kw)


def test_a_valid_dag_validates() -> None:
    graph = ExecutionGraph(
        nodes=(_node("a"), _node("b"), _node("verify", "a", "b", agent_id="python_auditor")),
    )
    graph.validate()  # no lanza
    assert graph.node("verify").agent_id == "python_auditor"


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(GraphError, match="duplicado"):
        ExecutionGraph(nodes=(_node("a"), _node("a"))).validate()


def test_a_missing_dependency_is_rejected() -> None:
    with pytest.raises(GraphError, match="no existe"):
        ExecutionGraph(nodes=(_node("a", "fantasma"),)).validate()


def test_a_cycle_is_detected() -> None:
    with pytest.raises(GraphError, match="ciclo"):
        ExecutionGraph(nodes=(_node("a", "b"), _node("b", "a"))).validate()


def test_a_node_cannot_depend_on_itself() -> None:
    with pytest.raises(GraphError, match="sí mismo"):
        ExecutionGraph(nodes=(_node("a", "a"),)).validate()


def test_an_empty_graph_is_rejected() -> None:
    with pytest.raises(GraphError, match="no tiene nodos"):
        ExecutionGraph(nodes=()).validate()


def test_the_node_count_limit_is_enforced() -> None:
    nodes = tuple(_node(f"n{i}") for i in range(5))
    with pytest.raises(GraphError, match="máximo"):
        ExecutionGraph(nodes=nodes, limits=GraphLimits(max_nodes=4)).validate()


def test_bad_run_if_is_rejected() -> None:
    with pytest.raises(GraphError, match="run_if"):
        ExecutionGraph(nodes=(_node("a", run_if="maybe"),)).validate()


def test_ready_returns_nodes_whose_deps_are_done() -> None:
    graph = ExecutionGraph(nodes=(_node("a"), _node("b"), _node("c", "a", "b")))
    graph.validate()

    # Al empezar, a y b están listos; c espera.
    assert {n.id for n in graph.ready(done=set(), started=set())} == {"a", "b"}
    # Con a hecho pero b en curso, c sigue esperando y a/b no se repiten.
    assert {n.id for n in graph.ready(done={"a"}, started={"a", "b"})} == set()
    # Con a y b hechos, c está listo.
    assert {n.id for n in graph.ready(done={"a", "b"}, started={"a", "b"})} == {"c"}


def test_roundtrip_through_dict() -> None:
    graph = ExecutionGraph(
        nodes=(
            _node("a", task={"goal": "edita a.py"}),
            _node("verify", "a", agent_id="python_auditor", max_retries=2, run_if="any_done"),
        ),
        limits=GraphLimits(max_nodes=10, timeout_s=600),
    )
    graph.validate()

    restored = ExecutionGraph.from_dict(graph.to_dict())

    assert restored.to_dict() == graph.to_dict()
    assert restored.node("a").task == {"goal": "edita a.py"}
    assert restored.node("verify").run_if == "any_done"
    assert restored.limits.timeout_s == 600


def test_from_dict_validates() -> None:
    with pytest.raises(GraphError):
        ExecutionGraph.from_dict({"nodes": [{"id": "a", "agent_id": "x", "depends_on": ["z"]}]})
    with pytest.raises(GraphError, match="lista 'nodes'"):
        ExecutionGraph.from_dict({"limits": {}})
