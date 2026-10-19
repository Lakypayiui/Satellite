"""Punto 4 (auditoría) — cobertura y coherencia del contexto estático entre flujos.

El índice persistente del proyecto (``.satellite/context/index.json``) es estructural y
bajo demanda: se adjunta una vista acotada a cada turno, y quien consulta lee solo los
archivos que el índice elige. Estas pruebas reproducen la brecha reportada: que un
proyecto recién indexado, al preguntar «¿De qué trata el proyecto?», envíe al proveedor
extractos **reales** del proyecto por las tres rutas (chat directo, planner y ejecución)
y que las preguntas normales recuperen archivos relevantes desde todas las interfaces.

Cubren además los casos que no deben regresar: proyecto **sin índice** (se dice, no se
finge) y archivos **sensibles** o de **fuera** del proyecto (no se leen como contexto).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.agents.registry import DefaultAgentRegistry
from satellite.context.engine import DefaultContextEngine
from satellite.context.mentions import parse
from satellite.context.project_index import ProjectIndex
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.context_models import ContextRefKind
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import GraphRunner, Planner, PlanningResult, RuntimeServices
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.orchestration.runner import DefaultGraphRunner
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


# --- dobles -------------------------------------------------------------------


class _PlannerQueNoPlanifica(Planner):
    async def plan(self, task, catalog, context_hint=()):
        raise AssertionError("una respuesta directa no debe planificar")


class _RunnerQueNoCorre(GraphRunner):
    async def run(self, graph, task, services):
        raise AssertionError("una respuesta directa no debe ejecutar el grafo")


class _PlannerFijo(Planner):
    """Devuelve siempre el mismo grafo (una sola vez: no replanifica)."""

    def __init__(self, graph: ExecutionGraph) -> None:
        self.graph = graph
        self.llamadas = 0

    async def plan(self, task, catalog, context_hint=()):
        self.llamadas += 1
        return PlanningResult(kind="plan", graph=self.graph)


class _AgenteGrabador(Agent):
    """Agente de mentira: se queda con el contexto que recibe."""

    def __init__(self, spec_id: str) -> None:
        self.spec_id = spec_id
        self.last_ctx: AgentContext | None = None

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.last_ctx = ctx
        return AgentResult(
            node_id=ctx.node_id, spec_id=self.spec_id, status=AgentStatus.OK, summary="ok"
        )


class _FactoryGrabadora(AgentFactory):
    def __init__(self) -> None:
        self.agents: dict[str, _AgenteGrabador] = {}

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        self.agents.setdefault(spec_id, _AgenteGrabador(spec_id))
        return self.agents[spec_id]


class _AdvisorGrabador:
    """Asesor de plan de mentira: guarda el historial/contexto que recibe."""

    def __init__(self) -> None:
        self.history = ""
        self.context = ""

    async def generate(self, task, *, context="", history="", feedback=""):
        self.context, self.history = context, history
        return None  # sin alternativas: basta con ver qué se envió


class _OrquestadorGrabador:
    """Orquestador de mentira para ChatSession (no arma runtime)."""

    def __init__(self) -> None:
        self.services = None
        self.tasks: list = []

    async def execute(self, task: Task) -> TaskResult:
        self.tasks.append(task)
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="listo")


def _one_node_graph() -> ExecutionGraph:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    return graph


def _proyecto(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(
        "# Tienda\nUn punto de venta con caja registradora y ticket.\n", encoding="utf-8"
    )
    (tmp_path / "caja.py").write_text(
        "def cobrar(total):\n    return total\n", encoding="utf-8"
    )
    return tmp_path


def _enviado(provider: FakeLLM) -> str:
    return "\n".join(message.content for message in provider.requests[-1].messages)


# --- el índice llega al proveedor por cada flujo ------------------------------


def test_explain_question_reaches_the_provider_with_the_readme(tmp_path: Path) -> None:
    """El atajo conversacional recibe el README real del proyecto indexado."""
    project = _proyecto(tmp_path)
    provider = FakeLLM(script=[{"content": "Es un punto de venta."}])
    services = RuntimeServices(
        registry=DefaultAgentRegistry(),
        provider=provider,
        context=DefaultContextEngine(project),
    )
    orch = DefaultOrchestrator(
        _PlannerQueNoPlanifica(), _RunnerQueNoCorre(), services, allow_chat_direct=True
    )

    run(orch.execute(Task(goal="¿De qué trata el proyecto?", project_root=project)))

    enviado = _enviado(provider)
    assert "caja registradora" in enviado  # el README, no una suposición
    assert "PROJECT CONTEXT" in enviado


def test_chat_session_attaches_the_index_before_sending(tmp_path: Path) -> None:
    """TUI y escritorio hablan por ChatSession: el índice tiene que ir en el turno."""
    project = _proyecto(tmp_path)
    provider = FakeLLM(script=[{"content": "Es un punto de venta."}])
    services = RuntimeServices(
        registry=DefaultAgentRegistry(),
        provider=provider,
        context=DefaultContextEngine(project),
    )
    orch = DefaultOrchestrator(
        _PlannerQueNoPlanifica(), _RunnerQueNoCorre(), services, allow_chat_direct=True
    )
    chat = ChatSession(project, orch, persist=False)

    reply = run(chat.send("¿De qué trata el proyecto?"))

    assert reply.status == "ok"
    assert "caja registradora" in _enviado(provider)


def test_the_planner_receives_the_project_context(tmp_path: Path) -> None:
    """El planner arma el grafo con el índice del proyecto en su slot dinámico."""
    project = _proyecto(tmp_path)
    provider = FakeLLM(script=[{"content": '{"type":"needs_clarification","question":"¿?"}'}])
    from satellite.orchestration.planner import LLMPlanner

    task = Task(goal="haz lo que haga falta", project_root=project)
    index = ProjectIndex(project)
    index.ensure_persisted()
    task.metadata["project_context"] = index.context_for_goal(task.goal)

    run(LLMPlanner(provider, DefaultAgentRegistry()).plan(task, DefaultAgentRegistry()))

    assert "caja registradora" in _enviado(provider)


def test_the_plan_mode_advisor_receives_the_project_context(tmp_path: Path) -> None:
    """El modo plan también ve el proyecto (no solo el planner normal)."""
    project = _proyecto(tmp_path)
    advisor = _AdvisorGrabador()
    services = RuntimeServices(registry=DefaultAgentRegistry(), context=DefaultContextEngine(project))
    orch = DefaultOrchestrator(
        _PlannerQueNoPlanifica(),
        _RunnerQueNoCorre(),
        services,
        allow_chat_direct=False,
        plan_advisor=advisor,
    )

    run(
        orch.execute(
            Task(goal="dime cómo montar esto", project_root=project, metadata={"plan_mode": True})
        )
    )

    assert "caja registradora" in advisor.history


def test_a_graph_node_receives_the_project_context(tmp_path: Path) -> None:
    """La ruta de ejecución: el nodo recibe el índice como sección dinámica."""
    project = _proyecto(tmp_path)
    factory = _FactoryGrabadora()
    services = RuntimeServices(
        registry=DefaultAgentRegistry(),
        factory=factory,
        context=DefaultContextEngine(project),
    )
    orch = DefaultOrchestrator(
        _PlannerFijo(_one_node_graph()),
        DefaultGraphRunner(factory),
        services,
        allow_chat_direct=False,
    )

    result = run(orch.execute(Task(goal="revisa caja.py", project_root=project)))

    assert result.status is TaskStatus.OK
    secciones = factory.agents["sA"].last_ctx.context.sections
    assert any("caja registradora" in section.content for section in secciones)


# --- preguntas normales recuperan archivos relevantes -------------------------


def test_a_normal_question_retrieves_the_relevant_file(tmp_path: Path) -> None:
    """Una pregunta normal trae al prompt el archivo del proyecto que el índice elige."""
    project = _proyecto(tmp_path)
    provider = FakeLLM(script=[{"content": "cobrar devuelve el total."}])
    services = RuntimeServices(
        registry=DefaultAgentRegistry(),
        provider=provider,
        context=DefaultContextEngine(project),
    )
    orch = DefaultOrchestrator(
        _PlannerQueNoPlanifica(), _RunnerQueNoCorre(), services, allow_chat_direct=True
    )

    run(orch.execute(Task(goal="¿qué hace caja.py?", project_root=project)))

    assert "def cobrar(total)" in _enviado(provider)


def test_the_same_index_view_is_bounded(tmp_path: Path) -> None:
    """La vista es acotada: no se mete todo el repositorio al prompt."""
    for i in range(60):
        (tmp_path / f"modulo_{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")

    vista = ProjectIndex(tmp_path).context_for_goal("¿de qué trata el proyecto?")

    assert len(vista) <= 12_000
    # El árbol no lista sin freno: se corta el listado a un tope razonable.
    assert vista.count("\n- ") <= 100


# --- proyecto sin índice -------------------------------------------------------


def test_an_empty_project_says_there_is_no_context(tmp_path: Path) -> None:
    """Sin archivos indexables, un turno explicativo lo dice en vez de fingir contexto."""
    provider = FakeLLM(script=[{"content": "No lo sé."}])
    services = RuntimeServices(
        registry=DefaultAgentRegistry(),
        provider=provider,
        context=DefaultContextEngine(tmp_path),
    )
    orch = DefaultOrchestrator(
        _PlannerQueNoPlanifica(), _RunnerQueNoCorre(), services, allow_chat_direct=True
    )
    chat = ChatSession(tmp_path, orch, persist=False)

    reply = run(chat.send("¿De qué trata el proyecto?"))

    assert "No hay contexto de proyecto" in reply.text


def _orchestrator_for(project: Path) -> DefaultOrchestrator:
    return DefaultOrchestrator(
        _PlannerQueNoPlanifica(),
        _RunnerQueNoCorre(),
        RuntimeServices(
            registry=DefaultAgentRegistry(),
            provider=FakeLLM(script=[{"content": "ok"}]),
            context=DefaultContextEngine(project),
        ),
        allow_chat_direct=True,
    )


def test_the_orchestrator_records_the_index_status(tmp_path: Path, monkeypatch) -> None:
    """El estado del índice viaja en la metadata (ok / empty / unavailable)."""
    project = _proyecto(tmp_path)
    tarea = Task(goal="¿de qué trata el proyecto?", project_root=project)
    run(_orchestrator_for(project).execute(tarea))
    assert tarea.metadata["project_context_status"] == "ok"

    vacio_dir = tmp_path / "nada"
    vacio_dir.mkdir()
    vacio = Task(goal="¿de qué trata el proyecto?", project_root=vacio_dir)
    run(_orchestrator_for(vacio_dir).execute(vacio))
    assert vacio.metadata["project_context_status"] == "empty"

    # El engine se arma antes de romper el índice: así el fallo ocurre al ejecutar.
    orch = _orchestrator_for(project)

    def explota(self):
        raise OSError("no se pudo indexar")

    monkeypatch.setattr(ProjectIndex, "ensure_persisted", explota)
    roto = Task(goal="¿de qué trata el proyecto?", project_root=project)
    run(orch.execute(roto))
    assert roto.metadata["project_context_status"] == "unavailable"


# --- sin regresión: archivos sensibles y de fuera -----------------------------


def test_sensitive_files_are_not_attached_and_are_reported(tmp_path: Path) -> None:
    """Un @ a un archivo sensible no se adjunta y no se calla: se dice."""
    (tmp_path / ".env").write_text("API_KEY=never-store", encoding="utf-8")
    (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")

    menciones = parse("mira @.env y @app.py", tmp_path)

    assert [ref.value for ref in menciones.refs] == ["app.py"]
    assert menciones.ignored == (".env",)
    assert "ignorado" in menciones.summary() and ".env" in menciones.summary()

    orch = _OrquestadorGrabador()
    chat = ChatSession(tmp_path, orch, persist=False)
    reply = run(chat.send("mira @.env"))

    assert "context_refs" not in orch.tasks[0].metadata
    assert "No adjunté @.env" in reply.text
    assert reply.context["ignored"] == [".env"]


def test_the_explain_question_never_leaks_a_secret(tmp_path: Path) -> None:
    """El índice nunca mete un secreto al prompt (regresión del punto 3)."""
    _proyecto(tmp_path)
    (tmp_path / ".env").write_text("API_KEY=never-store", encoding="utf-8")

    provider = FakeLLM(script=[{"content": "ok"}])
    services = RuntimeServices(
        registry=DefaultAgentRegistry(),
        provider=provider,
        context=DefaultContextEngine(tmp_path),
    )
    orch = DefaultOrchestrator(
        _PlannerQueNoPlanifica(), _RunnerQueNoCorre(), services, allow_chat_direct=True
    )

    run(orch.execute(Task(goal="¿De qué trata el proyecto?", project_root=tmp_path)))

    enviado = _enviado(provider)
    assert "never-store" not in enviado
    assert "API_KEY" not in enviado


def test_a_file_outside_the_project_is_not_read_without_approval(tmp_path: Path) -> None:
    """Una @mención de fuera no se lee ni se adjunta sin aprobación (regresión)."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x", encoding="utf-8")
    outside = tmp_path / "secreto.txt"
    outside.write_text("CONTENIDO_DE_FUERA", encoding="utf-8")

    orch = _OrquestadorGrabador()
    # El runtime real siempre trae un motor de contexto; sin puerto de aprobación, la
    # mención externa no se puede leer (estado ``no_port``).
    orch.services = RuntimeServices(context=DefaultContextEngine(project))
    chat = ChatSession(project, orch, persist=False)
    reply = run(chat.send(f"lee @{outside}"))

    metadata = orch.tasks[0].metadata
    assert "context_external" not in metadata
    assert "CONTENIDO_DE_FUERA" not in str(metadata.get("project_context", ""))
    assert reply.context["external"][0]["status"] == "no_port"


# --- paridad del informe de contexto entre superficies ------------------------


def test_the_context_report_has_the_same_shape_everywhere(tmp_path: Path) -> None:
    """ChatSession (TUI/CLI) y el backend de escritorio informan lo mismo."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from desktop_support import make_backend

    (tmp_path / "a" / "api.py").parent.mkdir(parents=True)
    (tmp_path / "a" / "api.py").write_text("x", encoding="utf-8")
    (tmp_path / "b" / "api.py").parent.mkdir(parents=True)
    (tmp_path / "b" / "api.py").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("K=1", encoding="utf-8")
    texto = "mira @api.py @.env @no_existe.py"

    chat = ChatSession(tmp_path, _OrquestadorGrabador(), persist=False)
    report_chat = chat._mention_report(parse(texto, tmp_path), [])
    report_desktop = make_backend(tmp_path)._mention_context(texto, tmp_path)

    assert set(report_chat) == set(report_desktop)
    for clave in ("refs", "missing", "ambiguous", "ignored", "external", "summary"):
        assert clave in report_chat
    assert report_chat["ignored"] == [".env"]
    assert report_chat["ambiguous"] and report_chat["ambiguous"][0]["value"] == "api.py"


def test_the_kind_of_an_attached_mention_is_reported(tmp_path: Path) -> None:
    """El informe dice el tipo (path/dir/símbolo) de lo adjuntado, como antes."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.py").write_text("x", encoding="utf-8")

    orch = _OrquestadorGrabador()
    chat = ChatSession(tmp_path, orch, persist=False)
    reply = run(chat.send("mira @src/api.py y @login"))

    kinds = {ref["value"]: ref["kind"] for ref in reply.context["refs"]}
    assert kinds == {"src/api.py": "path", "login": "symbol"}
    assert ContextRefKind.SYMBOL.value == "symbol"


def _tui_app(tmp_path: Path, monkeypatch):
    import json

    from satellite.tui.app import SatelliteTUI
    from satellite.tui.session import Session

    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
    )
    return SatelliteTUI(Session(tmp_path, home=home))


def test_the_tui_reports_an_ignored_mention(tmp_path: Path, monkeypatch) -> None:
    """La TUI no calla un @ a un archivo sensible: lo dice como *ignorado*."""

    async def scenario():
        (tmp_path / ".env").write_text("K=1", encoding="utf-8")
        app = _tui_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._do_chat("mira @.env")
            await pilot.pause()
            transcript = "\n".join(app.transcript)
            assert "contexto:" in transcript
            assert "ignorado" in transcript

    run(scenario())
