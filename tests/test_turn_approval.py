"""La aprobación pertenece al turno, no al servicio compartido del orquestador."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.runner import DefaultGraphRunner


def run(coro):
    return asyncio.run(coro)


class _Orquestador:
    """Captura la tarea de cada turno y deja que los dos se solapen."""

    def __init__(self, default_approval) -> None:
        self.services = SimpleNamespace(approval=default_approval)
        self.approvals: list[object | None] = []

    async def execute(self, task: Task) -> TaskResult:
        self.approvals.append(task.approval)
        await asyncio.sleep(0.01)
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


def test_concurrent_chat_turns_keep_their_own_approval(tmp_path: Path) -> None:
    default = object()
    primero = object()
    segundo = object()
    orchestrator = _Orquestador(default)
    chat_a = ChatSession(tmp_path, orchestrator, persist=False)
    chat_b = ChatSession(tmp_path, orchestrator, persist=False)

    async def escenario() -> None:
        await asyncio.gather(
            chat_a.send("primer turno", approval=primero),
            chat_b.send("segundo turno", approval=segundo),
        )

    run(escenario())

    assert {id(item) for item in orchestrator.approvals} == {id(primero), id(segundo)}
    # El puerto por defecto del servicio sigue intacto: nadie lo reemplazó.
    assert orchestrator.services.approval is default


class _Agente:
    def __init__(self, approvals: list[object | None]) -> None:
        self.approvals = approvals

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.approvals.append(ctx.approval)
        await asyncio.sleep(0.01)
        return AgentResult(
            node_id=ctx.node_id,
            spec_id="worker",
            status=AgentStatus.OK,
            summary="ok",
        )


class _Fabrica(AgentFactory):
    def __init__(self, approvals: list[object | None]) -> None:
        self.approvals = approvals

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        return _Agente(self.approvals)


def _grafo() -> ExecutionGraph:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="worker"))
    return graph


def test_concurrent_graph_runs_resolve_the_task_approval(tmp_path: Path) -> None:
    primero = object()
    segundo = object()
    approvals: list[object | None] = []
    services = RuntimeServices(factory=_Fabrica(approvals))
    runner = DefaultGraphRunner(services.factory)
    tarea_a = Task(goal="uno", project_root=tmp_path, approval=primero)
    tarea_b = Task(goal="dos", project_root=tmp_path, approval=segundo)

    async def escenario() -> None:
        await asyncio.gather(
            runner.run(_grafo(), tarea_a, services),
            runner.run(_grafo(), tarea_b, services),
        )

    run(escenario())

    assert {id(item) for item in approvals} == {id(primero), id(segundo)}
    assert services.approval is None


def test_the_approval_is_runtime_only_and_is_not_persisted(tmp_path: Path) -> None:
    port = object()
    task = Task(goal="x", project_root=tmp_path, approval=port)

    data = task.to_dict()

    assert "approval" not in data
    assert Task.from_dict(data).approval is None
