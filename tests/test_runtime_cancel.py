"""Cancelación cooperativa: token, runner, orquestador y handler de MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.core.graph import Edge, ExecutionGraph, Node
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task, TaskStatus
from satellite.events.base import EventSink
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.cancel import CancelToken, CancelledTask
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.orchestration.runner import DefaultGraphRunner


def run(coro):
    return asyncio.run(coro)


class Sink(EventSink):
    """Sink que guarda lo emitido."""

    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        return None


class AgenteQueCancela(Agent):
    """Cancela al terminar el nodo indicado, como haría el usuario a mitad de tarea."""

    def __init__(self, token: CancelToken, cancelar_en: str) -> None:
        self.token = token
        self.cancelar_en = cancelar_en
        self.nodos: list[str] = []

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.nodos.append(ctx.node_id)
        if ctx.node_id == self.cancelar_en:
            self.token.request("cancelado en la prueba")
        return AgentResult(
            node_id=ctx.node_id, spec_id="eco", status=AgentStatus.OK, summary="ok"
        )


class Factory(AgentFactory):
    """Fábrica que devuelve siempre el mismo agente, para contar qué nodos corrieron."""

    def __init__(self, agente: Agent) -> None:
        self.agente = agente

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        return self.agente


def grafo() -> ExecutionGraph:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="eco"))
    graph.add_node(Node(id="n2", spec_id="eco"))
    graph.add_edge(Edge(id="e1", source="n1", target="n2", payload="result"))
    return graph


# --- el token ---


def test_the_token_is_idempotent_and_resets() -> None:
    token = CancelToken()

    assert token.cancelled is False
    token.request("primera razón")
    token.request("segunda razón")

    assert token.cancelled is True
    assert token.reason == "primera razón"  # la primera es la que vale
    assert token.event().is_set() is True

    token.reset()

    assert token.cancelled is False and token.reason == ""


def test_the_token_raises_only_when_it_was_requested() -> None:
    token = CancelToken()

    token.raise_if_cancelled()  # sin cancelación pedida no levanta nada

    token.request("porque sí")

    with pytest.raises(CancelledTask):
        token.raise_if_cancelled()


# --- el runner ---


def test_cancelled_before_starting_stops_everything(tmp_path: Path) -> None:
    token = CancelToken()
    agente = AgenteQueCancela(token, cancelar_en="")
    sink = Sink()
    token.request("antes de empezar")

    resultado = run(
        DefaultGraphRunner(Factory(agente), sink=sink).run(
            grafo(), Task(goal="x", project_root=tmp_path), RuntimeServices(factory=Factory(agente), cancel=token, sink=sink)
        )
    )

    assert resultado.status is TaskStatus.CANCELLED
    assert agente.nodos == []  # no corrió ningún nodo
    assert "antes de empezar" in resultado.summary
    assert sink.events[-1].payload["status"] == "cancelled"


def test_cancelled_inside_a_node_stops_after_that_node(tmp_path: Path) -> None:
    token = CancelToken()
    agente = AgenteQueCancela(token, cancelar_en="n1")
    factory = Factory(agente)

    resultado = run(
        DefaultGraphRunner(factory).run(
            grafo(),
            Task(goal="x", project_root=tmp_path),
            RuntimeServices(factory=factory, cancel=token),
        )
    )

    assert agente.nodos == ["n1"]  # el segundo nodo no llegó a empezar
    assert resultado.status is TaskStatus.CANCELLED
    assert resultado.output["cancelled"] is True
    assert resultado.output["nodes_done"] == 1  # lo hecho queda contado
    assert resultado.error is None  # cancelar no es un fallo


def test_without_a_token_the_runner_behaves_as_before(tmp_path: Path) -> None:
    agente = AgenteQueCancela(CancelToken(), cancelar_en="")
    factory = Factory(agente)

    resultado = run(
        DefaultGraphRunner(factory).run(
            grafo(), Task(goal="x", project_root=tmp_path), RuntimeServices(factory=factory)
        )
    )

    assert resultado.status is TaskStatus.OK
    assert agente.nodos == ["n1", "n2"]


# --- el orquestador y MCP ---


class Nada:
    """Planner/runner de mentira: aquí sólo se prueba la puerta de la cancelación."""

    async def plan(self, *args, **kwargs):
        raise AssertionError("no debería planificar")

    async def run(self, *args, **kwargs):
        raise AssertionError("no debería ejecutar")


def test_the_orchestrator_cancel_reaches_its_token() -> None:
    token = CancelToken()
    orchestrator = DefaultOrchestrator(Nada(), Nada(), RuntimeServices(cancel=token))

    resultado = orchestrator.cancel("t1", "porque sí")

    assert resultado["cancelled"] is True
    assert resultado["task_id"] == "t1"
    assert token.reason == "porque sí"


def test_the_mcp_handler_asks_instead_of_just_forgetting(tmp_path: Path) -> None:
    """Antes sólo soltaba el runtime de la caché: no detenía nada."""
    from satellite.mcp.handlers import RuntimeBundle, get_handlers

    token = CancelToken()

    class Orquestador:
        def cancel(self, task_id: str = "", reason: str = "") -> dict:
            token.request(reason or "cancelado")
            return {"cancelled": True, "reason": token.reason}

    bundle = RuntimeBundle()
    root = str(tmp_path)
    bundle._runtimes[root] = {"orchestrator": Orquestador()}
    bundle._active["t1"] = root

    respuesta = run(get_handlers(bundle)["satellite_cancel"]({"task_id": "t1"}))

    assert respuesta["isError"] is False
    assert token.cancelled is True
    assert "cancelación pedida" in respuesta["text"]


def test_the_mcp_handler_says_when_there_is_nothing_to_cancel() -> None:
    from satellite.mcp.handlers import RuntimeBundle, get_handlers

    respuesta = run(get_handlers(RuntimeBundle())["satellite_cancel"]({"task_id": "t-nada"}))

    assert respuesta["isError"] is False
    assert "no hay ejecución que cancelar" in respuesta["text"]
