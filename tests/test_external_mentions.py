"""Menciones ``@`` a rutas externas: aviso + confirmación puntual, sin persistir nada.

Una ruta de fuera del proyecto sólo se lee si el usuario la autoriza en el turno;
si deniega o la interfaz no puede preguntar, no se lee y se avisa. La autorización
no viaja por metadata ni se reutiliza en el turno siguiente.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.approval.base import (
    ApprovalContext,
    ApprovalPort,
    AutoApprovalPort,
    DenyAllApprovalPort,
)
from satellite.context.engine import DefaultContextEngine
from satellite.context.mentions import parse
from satellite.core.context_models import ApprovedExternalContext, ContextRefKind
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import PlanningResult, RuntimeServices
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.orchestrator import DefaultOrchestrator, _append_external_context
from satellite.orchestration.runner import DefaultGraphRunner


def run(coro):
    return asyncio.run(coro)


# --- dobles ---


class _Servicios:
    def __init__(self, context) -> None:
        self.context = context


class _MotorContador:
    """Motor real que cuenta cuántas veces se resuelve (una lectura por petición)."""

    def __init__(self, project_root) -> None:
        self.engine = DefaultContextEngine(project_root)
        self.llamadas = 0
        self.ultimas_allowed = ()

    async def resolve(self, refs, limits=None, allowed_paths=()):
        self.llamadas += 1
        self.ultimas_allowed = tuple(allowed_paths)
        return await self.engine.resolve(refs, limits, allowed_paths)


class _Orquestador:
    """Orquestador de mentira: graba la tarea que recibe y responde directo."""

    def __init__(self, context) -> None:
        self.services = _Servicios(context)
        self.tasks = []

    async def execute(self, task):
        self.tasks.append(task)
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.OK,
            summary="ok",
            output={"mode": "direct_reply", "content": "respuesta"},
        )


class _Espia(ApprovalPort):
    def __init__(self, decision=None) -> None:
        self.calls = 0
        self.ctx: ApprovalContext | None = None
        self._decision = decision or ApprovalDecision(
            status=ApprovalStatus.APPROVED, reason="espía"
        )

    async def request(self, proposal, ctx) -> ApprovalDecision:
        self.calls += 1
        self.ctx = ctx
        return self._decision


def make_chat(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    motor = _MotorContador(project)
    orchestrator = _Orquestador(motor)
    return ChatSession(project, orchestrator, persist=False), motor, orchestrator


# --- parseo ---


def test_an_external_mention_is_split_out_of_refs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("x", encoding="utf-8")
    outside = tmp_path / "secreto.txt"
    outside.write_text("secreto", encoding="utf-8")

    menciones = parse(f"mira @a.py y @{outside}", project)

    assert [ref.value for ref in menciones.refs] == ["a.py"]
    assert len(menciones.external) == 1
    assert menciones.external[0].path == outside.resolve()
    assert menciones.external[0].kind is ContextRefKind.PATH
    assert menciones.missing == ()


def test_a_missing_external_mention_stays_missing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    ausente = tmp_path / "no_existe.txt"

    menciones = parse(f"mira @{ausente}", project)

    assert menciones.external == ()
    assert menciones.refs == ()
    assert menciones.missing == (str(ausente),)


# --- aprobación puntual ---


def test_approved_external_mention_is_read_once_and_attached(tmp_path: Path) -> None:
    chat, motor, orchestrator = make_chat(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("contenido externo", encoding="utf-8")

    reply = run(chat.send(f"lee @{outside}", approval=AutoApprovalPort()))

    task = orchestrator.tasks[0]
    externo = task.metadata["context_external"]
    assert isinstance(externo, ApprovedExternalContext)  # sólo la aprobación lo crea
    assert "contenido externo" in externo.text
    assert motor.llamadas == 1  # una sola lectura por petición
    assert motor.ultimas_allowed == (outside.resolve(),)
    assert reply.context["external"][0]["status"] == "approved"
    assert "Adjunté" in reply.text


def test_denied_external_mention_is_not_read(tmp_path: Path) -> None:
    chat, motor, orchestrator = make_chat(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("contenido externo", encoding="utf-8")

    reply = run(chat.send(f"lee @{outside}", approval=DenyAllApprovalPort()))

    task = orchestrator.tasks[0]
    assert "context_external" not in task.metadata
    assert motor.llamadas == 0
    assert reply.context["external"][0]["status"] == "denied"
    assert "no se autorizó" in reply.text
    assert "contenido externo" not in task.metadata.get("project_context", "")


def test_without_an_approval_port_it_warns_and_does_not_read(tmp_path: Path) -> None:
    chat, motor, orchestrator = make_chat(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("contenido externo", encoding="utf-8")

    reply = run(chat.send(f"lee @{outside}"))

    assert "context_external" not in orchestrator.tasks[0].metadata
    assert motor.llamadas == 0
    assert reply.context["external"][0]["status"] == "no_port"
    assert "no puede pedir confirmación" in reply.text


def test_internal_mention_does_not_ask_for_approval(tmp_path: Path) -> None:
    chat, _motor, _orchestrator = make_chat(tmp_path)
    (chat.project_root / "a.py").write_text("x", encoding="utf-8")
    espia = _Espia()

    reply = run(chat.send("mira @a.py", approval=espia))

    assert espia.calls == 0
    assert reply.context["external"] == []
    assert reply.context["refs"] == [{"value": "a.py", "kind": "path"}]


def test_the_approval_payload_is_the_expected_one(tmp_path: Path) -> None:
    chat, _motor, _orchestrator = make_chat(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("x", encoding="utf-8")
    captura = _Espia()

    run(chat.send(f"lee @{outside}", approval=captura))

    ctx = captura.ctx
    assert ctx is not None
    assert ctx.kind == "context_path"
    assert ctx.proposal_id.startswith("ctxext-")  # único por petición
    assert ctx.details["tool"] == "mention"
    assert ctx.details["path"] == str(outside.resolve())
    assert ctx.details["requested"] == str(outside)
    assert ctx.details["consequence"] == "lectura de contexto (solo este turno)"
    assert str(outside.resolve()) in ctx.summary
    assert "solo este turno" in ctx.summary


def test_approval_does_not_leak_to_the_next_turn(tmp_path: Path) -> None:
    chat, motor, orchestrator = make_chat(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("contenido externo", encoding="utf-8")

    primero = run(chat.send(f"lee @{outside}", approval=AutoApprovalPort()))
    segundo = run(chat.send(f"lee @{outside}"))  # sin puerto: no autorizado

    assert "context_external" in orchestrator.tasks[0].metadata
    assert "context_external" not in orchestrator.tasks[1].metadata
    assert motor.llamadas == 1  # sólo se leyó en el turno autorizado
    assert primero.context["external"][0]["status"] == "approved"
    assert segundo.context["external"][0]["status"] == "no_port"
    assert "Adjunté" not in segundo.text


# --- el contenido llega a la respuesta directa y a los nodos ---


class _Proveedor:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, request):
        self.prompts.append(request.messages[-1].content)
        return SimpleNamespace(content="respuesta", usage=None)


def test_direct_reply_prompt_carries_the_authorized_content(tmp_path: Path) -> None:
    task = Task(
        goal="¿qué dice?",
        project_root=tmp_path,
        metadata={"context_external": ApprovedExternalContext("--- /x ---\nSECRETO_EXTERNO")},
    )
    _append_external_context(task)
    assert "SECRETO_EXTERNO" in task.metadata["project_context"]

    proveedor = _Proveedor()
    orchestrator = DefaultOrchestrator(
        planner=None, runner=None, services=SimpleNamespace(provider=proveedor)
    )
    run(orchestrator._answer_direct(task))

    assert "SECRETO_EXTERNO" in proveedor.prompts[0]


class _Store:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    async def save_task(self, task) -> None:
        self.saved.append(task.to_dict())

    async def save_state(self, task_id: str, state: dict) -> None:
        pass


class _PlannerAclaracion:
    async def plan(self, task, catalog) -> PlanningResult:
        return PlanningResult(kind="needs_clarification", question="¿?")


def test_external_content_is_used_but_not_persisted(tmp_path: Path) -> None:
    store = _Store()
    orchestrator = DefaultOrchestrator(
        planner=_PlannerAclaracion(),
        runner=None,
        services=SimpleNamespace(provider=None, registry=None),
        store=store,
        allow_chat_direct=False,
    )
    task = Task(
        goal="algo",
        project_root=tmp_path,
        metadata={"context_external": ApprovedExternalContext("SECRETO_EXTERNO")},
    )

    run(orchestrator.execute(task))

    # Se usó en el turno (llegó a project_context)…
    assert "SECRETO_EXTERNO" in task.metadata["project_context"]
    # …pero no se guardó con la tarea (es de un solo turno).
    assert "context_external" not in store.saved[0]["metadata"]


class _AgenteGrabador(Agent):
    def __init__(self, spec_id: str) -> None:
        self.spec_id = spec_id
        self.ctx: AgentContext | None = None

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.ctx = ctx
        return AgentResult(
            node_id=ctx.node_id, spec_id=self.spec_id, status=AgentStatus.OK, summary="ok"
        )


class _FactoryGrabadora(AgentFactory):
    def __init__(self) -> None:
        self.agents: dict[str, _AgenteGrabador] = {}

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        self.agents.setdefault(spec_id, _AgenteGrabador(spec_id))
        return self.agents[spec_id]


def _grafo_de_un_nodo() -> ExecutionGraph:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    return graph


def test_the_node_receives_the_authorized_content(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    engine = DefaultContextEngine(project)
    factory = _FactoryGrabadora()
    task = Task(
        goal="x",
        project_root=project,
        metadata={"context_external": ApprovedExternalContext("--- /x ---\nSECRETO_EXTERNO")},
    )
    # El orquestador es quien fusiona el contenido externo en `project_context`.
    _append_external_context(task)

    run(
        DefaultGraphRunner(factory).run(
            _grafo_de_un_nodo(), task, RuntimeServices(factory=factory, context=engine)
        )
    )

    secciones = factory.agents["sA"].ctx.context.sections
    assert any("SECRETO_EXTERNO" in seccion.content for seccion in secciones)


def test_metadata_cannot_forge_authorization_to_read_outside(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "secreto.txt"
    outside.write_text("SECRETO_EXTERNO", encoding="utf-8")
    engine = DefaultContextEngine(project)
    factory = _FactoryGrabadora()
    # Metadata libre con un ref externo: no autoriza nada, el resolver bloquea la lectura.
    task = Task(
        goal="x",
        project_root=project,
        metadata={"context_refs": [{"kind": "path", "value": str(outside)}]},
    )

    run(
        DefaultGraphRunner(factory).run(
            _grafo_de_un_nodo(), task, RuntimeServices(factory=factory, context=engine)
        )
    )

    secciones = factory.agents["sA"].ctx.context.sections
    assert all("SECRETO_EXTERNO" not in seccion.content for seccion in secciones)


# --- la aprobación es la ÚNICA fuente del contexto externo ---


def test_orchestrator_ignores_raw_context_external_in_metadata(tmp_path: Path) -> None:
    """Un texto suelto en metadata['context_external'] no se adjunta: se ignora."""
    task = Task(
        goal="x",
        project_root=tmp_path,
        metadata={"context_external": "INYECTADO", "project_context": "índice"},
    )

    _append_external_context(task)

    assert "INYECTADO" not in task.metadata["project_context"]


def test_extra_metadata_cannot_inject_external_context(tmp_path: Path) -> None:
    """`extra_metadata` no puede aportar contexto externo: esas claves son reservadas."""
    chat, _motor, orchestrator = make_chat(tmp_path)

    run(
        chat.send(
            "hola",
            extra_metadata={
                # ni un texto suelto ni el propio envoltorio colado por metadata libre
                "context_external": ApprovedExternalContext("INYECTADO"),
                "context_refs": [{"kind": "path", "value": "/etc/hosts"}],
            },
        )
    )

    metadata = orchestrator.tasks[0].metadata
    assert "context_external" not in metadata
    assert "context_refs" not in metadata
    assert "INYECTADO" not in metadata.get("project_context", "")


def test_extra_metadata_cannot_bypass_a_denied_mention(tmp_path: Path) -> None:
    """Aunque se deniegue la mención, no se cuela contenido externo por metadata."""
    chat, motor, orchestrator = make_chat(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("contenido externo", encoding="utf-8")

    run(
        chat.send(
            f"lee @{outside}",
            approval=DenyAllApprovalPort(),
            extra_metadata={"context_external": "INYECTADO"},
        )
    )

    task = orchestrator.tasks[0]
    assert "context_external" not in task.metadata
    _append_external_context(task)  # ni aun forzando el merge
    assert "INYECTADO" not in task.metadata.get("project_context", "")
    assert "contenido externo" not in task.metadata.get("project_context", "")
    assert motor.llamadas == 0


def test_the_authorized_external_file_is_not_indexed(tmp_path: Path) -> None:
    """Leer fuera se queda en el turno: no entra al índice (ni memoria ni disco)."""
    chat, motor, _orchestrator = make_chat(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("contenido externo", encoding="utf-8")

    run(chat.send(f"lee @{outside}", approval=AutoApprovalPort()))

    index = motor.engine.resolver.index
    ruta = str(outside.resolve())
    assert ruta not in index._stats
    assert ruta not in index._structure
    assert not index.index_path.exists() or ruta not in index.index_path.read_text(encoding="utf-8")


# --- payload que consume la UI ---


def test_desktop_offers_only_once_for_context_path() -> None:
    from satellite.desktop.approvals import OPTIONS_BY_KIND, DesktopApproval

    assert OPTIONS_BY_KIND["context_path"] == ("once", "reject")
    port = DesktopApproval()

    async def scenario():
        tarea = asyncio.create_task(
            port.request(
                {"kind": "context_path", "path": "/x"},
                ApprovalContext(kind="context_path", summary="adjuntar /x", details={"path": "/x"}),
            )
        )
        await asyncio.sleep(0)  # deja que la petición quede pendiente
        descrito = port.describe()
        port.submit(
            descrito[0]["proposal_id"],
            ApprovalDecision(status=ApprovalStatus.APPROVED, reason="ok", feedback="once"),
        )
        await tarea
        return descrito

    descrito = run(scenario())

    assert descrito[0]["kind"] == "context_path"
    assert descrito[0]["options"] == ["once", "reject"]
