"""Fase 4 (docs/PLAN-grafo-piloto.md): el grafo es inspeccionable y reanudable.

El ejecutor salta los nodos ya hechos que se le pasan y llama al checkpoint al terminar
cada uno; el almacén de checkpoints guarda por forma del grafo y lo reanuda.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentFactory
from satellite.agents.registry import DefaultAgentRegistry
from satellite.approval.base import AutoApprovalPort
from satellite.core.result import AgentResult, AgentStatus, ExecutionMetrics
from satellite.core.spec import AgentSpec
from satellite.orchestration.delegation import DelegationManager
from satellite.orchestration.graph import ExecutionGraph, GraphNode
from satellite.orchestration.graph_checkpoint import GraphCheckpoint, graph_key
from satellite.orchestration.graph_executor import GraphExecutor


def run(coro):
    return asyncio.run(coro)


class _Agent(Agent):
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def execute(self, task, ctx):
        self.ran.append(str(task.get("goal")))
        return AgentResult(node_id="n", spec_id="x", status=AgentStatus.OK, summary="ok",
                           output={}, metrics=ExecutionMetrics(tokens_in=1, tokens_out=1))


class _Factory(AgentFactory):
    def __init__(self, agent): self.agent = agent
    def create(self, spec_id, *, model_role=None): return self.agent


def _manager(agent):
    reg = DefaultAgentRegistry()
    reg.register_spec(AgentSpec(id="python_coder", name="c", capabilities=("c",), prompt_ref="c"))
    return DelegationManager(_Factory(agent), registry=reg, approval=AutoApprovalPort())


def _graph():
    return ExecutionGraph(nodes=(
        GraphNode(id="a", agent_id="python_coder", task={"goal": "a"}),
        GraphNode(id="b", agent_id="python_coder", task={"goal": "b"}, depends_on=("a",)),
    ))


def test_resume_skips_already_done_nodes() -> None:
    agent = _Agent()
    manager = _manager(agent)
    resume = {"a": {"agent_id": "python_coder", "status": "ok", "ok": True, "summary": "ya"}}

    result = run(GraphExecutor(manager).run(
        _graph(), parent_ctx=manager.root_context("t"), project_root=Path("."),
        caller_spec=None, resume=resume,
    ))

    assert agent.ran == ["b"]  # a no se repite
    assert result.outcomes["a"].ok and result.outcomes["b"].ok


def test_the_checkpoint_is_called_per_finished_node() -> None:
    saved: list[str] = []
    manager = _manager(_Agent())

    run(GraphExecutor(manager).run(
        _graph(), parent_ctx=manager.root_context("t"), project_root=Path("."),
        caller_spec=None, checkpoint=lambda nid, outcome: saved.append(nid),
    ))

    assert saved == ["a", "b"]


def test_the_checkpoint_store_persists_and_resumes_by_graph_shape(tmp_path: Path) -> None:
    store = GraphCheckpoint(tmp_path)
    g = _graph().to_dict()
    key = graph_key(g)

    store.save_node(key, "a", {"id": "a", "agent_id": "python_coder", "status": "ok", "ok": True})
    # Un estado no terminado no se guarda (no se reanuda algo a medias como hecho).
    store.save_node(key, "b", {"id": "b", "status": "failed", "ok": False})

    assert set(store.load(key)) == {"a"}
    # Otra forma de grafo no comparte checkpoint.
    other = graph_key({"nodes": [{"id": "z", "agent_id": "x", "depends_on": [], "task": {"goal": "z"}}]})
    assert store.load(other) == {}

    store.clear(key)
    assert store.load(key) == {}


def test_graph_key_is_stable_for_the_same_shape() -> None:
    g1 = _graph().to_dict()
    g2 = _graph().to_dict()
    assert graph_key(g1) == graph_key(g2)
