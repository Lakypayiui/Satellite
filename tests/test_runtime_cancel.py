"""Cancelación cooperativa: token, runner, orquestador y handler de MCP."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.agents.runtime import LLMAgent
from satellite.core.graph import Edge, ExecutionGraph, Node
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskStatus
from satellite.events.base import EventSink
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.cancel import CancelToken, CancelledTask, race_cancel
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.orchestration.runner import DefaultGraphRunner
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import ToolContext
from satellite.tools.primitives import RunPythonTool
from satellite.tools.registry import DefaultToolRegistry


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


# --- la carrera con una llamada en vuelo (tanda 2) ---


class AgenteQueDevuelve(Agent):
    """Agente que devuelve el estado que se le diga."""

    def __init__(self, status: AgentStatus) -> None:
        self.status = status
        self.nodos: list[str] = []

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.nodos.append(ctx.node_id)
        return AgentResult(node_id=ctx.node_id, spec_id="eco", status=self.status, summary="hecho")


def test_a_node_that_ends_cancelled_makes_the_task_cancelled(tmp_path: Path) -> None:
    """El final del runner no puede decir «ok» si un nodo acabó cancelado: sería mentir."""
    agente = AgenteQueDevuelve(AgentStatus.CANCELLED)
    factory = Factory(agente)

    resultado = run(
        DefaultGraphRunner(factory).run(
            grafo(), Task(goal="x", project_root=tmp_path), RuntimeServices(factory=factory)
        )
    )

    assert resultado.status is TaskStatus.CANCELLED
    assert "cancelado" in resultado.summary.lower()


def test_race_cancel_lets_the_value_through_when_nobody_cancels() -> None:
    async def lento():
        await asyncio.sleep(0.01)
        return "listo"

    valor, cancelado = run(race_cancel(CancelToken(), lento()))

    assert (valor, cancelado) == ("listo", False)


def test_race_cancel_aborts_the_call_in_flight() -> None:
    """Si gana la cancelación, la llamada se abandona: no se espera a que acabe."""
    completadas: list[str] = []

    async def lenta():
        await asyncio.sleep(30)
        completadas.append("no debería llegar")
        return "tarde"

    async def escenario():
        token = CancelToken()
        tarea = asyncio.create_task(race_cancel(token, lenta()))
        await asyncio.sleep(0.05)
        token.request("cortado")
        valor, cancelado = await asyncio.wait_for(tarea, timeout=2)
        return valor, cancelado, completadas

    valor, cancelado, completadas = run(escenario())

    assert valor is None and cancelado is True
    assert completadas == []  # se abortó, no se esperó a los 30 s


class ProveedorLento:
    """Proveedor que no contesta: sólo lo corta la cancelación."""

    def __init__(self) -> None:
        self.empezadas = 0

    async def complete(self, request):
        self.empezadas += 1
        await asyncio.Event().wait()  # nunca contesta
        raise AssertionError("no debería contestar")


def make_agente(tmp_path: Path, proveedor) -> LLMAgent:
    policy = DefaultSecurityPolicy(allowed_tools={"request_context"})
    return LLMAgent(
        spec=AgentSpec(id="eco", name="Eco", capabilities=("eco",), prompt_ref="eco"),
        system_prompt="Eres un escritor.\n{tools}\n{output_schema}",
        user_template="Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
        tools=(),
        provider=proveedor,
        tool_registry=DefaultToolRegistry(policy=policy),
        policy=policy,
        project_root=tmp_path,
    )


def test_cancelling_during_a_llm_call_stops_the_node(tmp_path: Path) -> None:
    proveedor = ProveedorLento()
    agente = make_agente(tmp_path, proveedor)
    token = CancelToken()

    async def escenario():
        ctx = AgentContext(task_id="t1", node_id="n1", cancel=token)
        tarea = asyncio.create_task(agente.execute({"node_id": "n1", "goal": "x"}, ctx))
        for _ in range(100):
            if proveedor.empezadas:
                break
            await asyncio.sleep(0.01)
        token.request("cortado en la prueba")
        return await asyncio.wait_for(tarea, timeout=5)

    resultado = run(escenario())

    assert resultado.status is AgentStatus.CANCELLED
    assert resultado.output["cancelled"] is True
    assert "cortado en la prueba" in resultado.summary


def test_cancelled_before_the_node_costs_nothing(tmp_path: Path) -> None:
    proveedor = ProveedorLento()
    agente = make_agente(tmp_path, proveedor)
    token = CancelToken()
    token.request("ya venía cancelado")

    resultado = run(
        agente.execute(
            {"node_id": "n1", "goal": "x"},
            AgentContext(task_id="t1", node_id="n1", cancel=token),
        )
    )

    assert resultado.status is AgentStatus.CANCELLED
    assert proveedor.empezadas == 0  # no se gastó ni una llamada


# --- el subproceso en marcha (tanda 3) ---


def test_execute_argv_terminates_the_process_when_cancelled(tmp_path: Path) -> None:
    """Un comando en marcha se termina: no se espera a que acabe por su cuenta."""
    from satellite.tools.terminal import execute_argv

    token = CancelToken()

    async def escenario():
        tarea = asyncio.create_task(
            execute_argv(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                env=os.environ.copy(),
                timeout_s=60,
                cancel=token,
            )
        )
        await asyncio.sleep(0.4)  # que le dé tiempo a arrancar
        empezado = time.monotonic()
        token.request("cortado en la prueba")
        resultado = await asyncio.wait_for(tarea, timeout=10)
        return resultado, time.monotonic() - empezado

    resultado, tardanza = run(escenario())

    assert resultado.success is False
    assert resultado.error_code == "cancelled"
    assert "cortado en la prueba" in (resultado.error or "")
    assert tardanza < 5  # no esperó a los 30 s del comando


def test_without_a_token_a_command_is_not_interrupted(tmp_path: Path) -> None:
    from satellite.tools.terminal import execute_argv

    resultado = run(
        execute_argv(
            [sys.executable, "-c", "print('hola')"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout_s=30,
        )
    )

    assert resultado.success is True
    assert "hola" in resultado.output["stdout"]


def test_run_python_stops_when_the_context_says_cancel(tmp_path: Path) -> None:
    """La tool real, con el token en su contexto: el proceso se termina."""
    token = CancelToken()
    policy = DefaultSecurityPolicy(
        allowed_tools={"run_python"}, allow_terminal=True, allowed_commands=(sys.executable,)
    )
    ctx = ToolContext(project_root=tmp_path, workdir=tmp_path, policy=policy, cancel=token)

    async def escenario():
        tarea = asyncio.create_task(
            RunPythonTool().execute(
                {"code": "import time; time.sleep(30)", "timeout_s": 60}, ctx
            )
        )
        await asyncio.sleep(0.4)
        token.request("cortado desde el contexto")
        return await asyncio.wait_for(tarea, timeout=10)

    resultado = run(escenario())

    assert resultado.success is False
    assert resultado.error_code == "cancelled"


def test_the_agent_stops_a_long_command_and_ends_cancelled(tmp_path: Path) -> None:
    """El recorrido completo: el agente lanza un comando largo y la cancelación lo corta."""
    policy = DefaultSecurityPolicy(
        allowed_tools={"run_python", "request_context"},
        allow_terminal=True,
        allowed_commands=(sys.executable,),
    )
    registry = DefaultToolRegistry(policy=policy)
    registry.register(RunPythonTool())
    spec = AgentSpec(
        id="runner",
        name="Runner",
        capabilities=("run",),
        prompt_ref="runner",
        tools=("run_python",),
        max_turns=2,
    )
    proveedor = FakeLLM(
        script=[
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "run_python",
                        "arguments": {"code": "import time; time.sleep(30)", "timeout_s": 60},
                    }
                ]
            },
            {"content": "ya está"},
        ]
    )
    agente = LLMAgent(
        spec=spec,
        system_prompt="Eres un ejecutor.\n{tools}\n{output_schema}",
        user_template="Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
        tools=registry.list_for(list(spec.tools)),
        provider=proveedor,
        tool_registry=registry,
        policy=policy,
        project_root=tmp_path,
    )
    token = CancelToken()

    async def escenario():
        ctx = AgentContext(task_id="t1", node_id="n1", cancel=token)
        tarea = asyncio.create_task(agente.execute({"node_id": "n1", "goal": "algo largo"}, ctx))
        await asyncio.sleep(0.6)  # el comando ya está corriendo
        empezado = time.monotonic()
        token.request("cortado durante el comando")
        resultado = await asyncio.wait_for(tarea, timeout=15)
        return resultado, time.monotonic() - empezado

    resultado, tardanza = run(escenario())

    assert resultado.status is AgentStatus.CANCELLED
    assert "cortado durante el comando" in resultado.summary
    assert tardanza < 8  # el comando de 30 s no llegó a terminar
    assert resultado.tool_calls and resultado.tool_calls[0].success is False


# --- las interfaces (tanda 4) ---


class OrchestratorFake:
    """Orquestador de mentira que apunta lo que le piden."""

    def __init__(self) -> None:
        self.peticiones: list[str] = []

    def cancel(self, task_id: str = "", reason: str = "") -> dict:
        self.peticiones.append(reason)
        return {"cancelled": True, "task_id": task_id, "reason": reason or "cancelado"}


def _tui_de_prueba(tmp_path, monkeypatch, runtime=None, roto: bool = False):
    import json as _json

    from satellite.tui.app import SatelliteTUI
    from satellite.tui.session import Session

    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        _json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
    )
    session = Session(tmp_path, home=home)
    if runtime is not None:
        session._runtime = runtime  # runtime ya armado, sin proveedor de verdad
    if roto:

        def sin_runtime(*_args, **_kwargs):
            raise RuntimeError("no se pudo armar el runtime")

        session.runtime = sin_runtime  # type: ignore[method-assign]
    return SatelliteTUI(session)


def test_the_tui_cancel_command_asks_the_runtime(tmp_path, monkeypatch) -> None:
    from satellite.events.bus import EventBus

    orquestador = OrchestratorFake()
    app = _tui_de_prueba(tmp_path, monkeypatch, (orquestador, EventBus(), None, None))

    async def escenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_cancel("prueba")
            await pilot.pause()
        return "\n".join(app.transcript)

    salida = asyncio.run(escenario())

    assert "cancelación pedida" in salida
    assert orquestador.peticiones == ["prueba"]


def test_the_tui_says_when_there_is_no_runtime_to_cancel(tmp_path, monkeypatch) -> None:
    """Si el runtime no se puede armar, se dice, no se finge que se canceló."""
    app = _tui_de_prueba(tmp_path, monkeypatch, roto=True)

    async def escenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_cancel("")
            await pilot.pause()
        return "\n".join(app.transcript)

    assert "no se pudo cancelar" in asyncio.run(escenario())


def test_the_cli_ctrl_c_asks_then_forces() -> None:
    """La primera pulsación pide cancelar; la segunda corta, para no dejar a nadie atrapado."""
    from satellite.cli.app import _cancel_handler

    orquestador = OrchestratorFake()
    handler = _cancel_handler(orquestador, "desde la prueba")

    handler(None, None)  # primera: pide la cancelación

    assert orquestador.peticiones == ["desde la prueba"]
    with pytest.raises(KeyboardInterrupt):
        handler(None, None)  # segunda: corta


def test_the_cli_handler_without_cancel_support_does_not_swallow_ctrl_c() -> None:
    """Sin soporte de cancelación, Ctrl+C no puede quedarse en no hacer nada."""
    from satellite.cli.app import _cancel_handler

    handler = _cancel_handler(object(), "x")

    with pytest.raises(KeyboardInterrupt):
        handler(None, None)


# --- el sandbox y las skills ---


class SinkDeSkills(EventSink):
    """Sink que guarda los eventos emitidos."""

    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        return None


class SandboxQueCancela:
    """Sandbox que dice que le cancelaron, sin lanzar nada."""

    level = None  # se rellena abajo

    async def run(self, argv, spec):
        raise AssertionError("no debería ejecutar nada")


def test_the_subprocess_sandbox_terminates_the_script(tmp_path: Path) -> None:
    """El sandbox es quien sabe terminar lo que arrancó: N0 corta el script en marcha."""
    from satellite.sandbox.base import SandboxSpec
    from satellite.sandbox.subprocess_sandbox import SubprocessSandbox

    token = CancelToken()

    async def escenario():
        spec = SandboxSpec(project_root=tmp_path, timeout_s=60, cancel=token)
        tarea = asyncio.create_task(
            SubprocessSandbox().run([sys.executable, "-c", "import time; time.sleep(30)"], spec)
        )
        await asyncio.sleep(0.4)
        empezado = time.monotonic()
        token.request("cortado en el sandbox")
        resultado = await asyncio.wait_for(tarea, timeout=10)
        return resultado, time.monotonic() - empezado

    resultado, tardanza = run(escenario())

    assert resultado.cancelled is True
    assert resultado.success is False
    assert "cortado en el sandbox" in (resultado.error or "")
    assert tardanza < 5  # no esperó a los 30 s


def test_the_host_sandbox_passes_the_token_along(tmp_path: Path) -> None:
    """N2 delega en N0: si el token no viaja en el spec, cancelar ahí no cortaría nada."""
    from satellite.sandbox.base import SandboxSpec
    from satellite.sandbox.host_sandbox import HostSandbox

    token = CancelToken()

    async def escenario():
        spec = SandboxSpec(project_root=tmp_path, timeout_s=60, cancel=token)
        tarea = asyncio.create_task(
            HostSandbox().run([sys.executable, "-c", "import time; time.sleep(30)"], spec)
        )
        await asyncio.sleep(0.4)
        token.request("cortado en N2")
        return await asyncio.wait_for(tarea, timeout=10)

    resultado = run(escenario())

    assert resultado.cancelled is True
    assert "cortado en N2" in (resultado.error or "")


def test_the_docker_argv_can_be_named_to_stop_the_container(tmp_path: Path) -> None:
    """Para parar el contenedor hay que nombrarlo: matar al cliente lo dejaría vivo."""
    from satellite.sandbox.base import SandboxSpec
    from satellite.sandbox.docker_sandbox import DockerSandbox

    sandbox = DockerSandbox()
    base = sandbox.build_argv(["python", "-c", "1"], SandboxSpec(project_root=tmp_path))

    nombrado = sandbox.named_argv(base, "satellite-sandbox-abcd1234")

    assert nombrado[0] == "docker" and nombrado[1] == "run"
    assert nombrado[nombrado.index("--name") + 1] == "satellite-sandbox-abcd1234"
    assert nombrado.index("--name") < nombrado.index(sandbox.image)  # antes de la imagen
    assert "--name" not in base  # el argv original no se toca


def test_a_cancelled_skill_is_not_reported_as_a_failure(tmp_path: Path) -> None:
    """Cancelar no es fallar, ni es ejecutar: no se emite ni SKILL_FAILED ni SKILL_EXECUTED."""
    from satellite.core.events import EventType
    from satellite.sandbox.base import SandboxLevel, SandboxResult
    from satellite.skills.models import SkillManifest
    from satellite.skills.registry import SkillRegistry
    from satellite.skills.runner import SkillRunner

    class Sandbox(SandboxQueCancela):
        level = SandboxLevel.N0

        async def run(self, argv, spec):
            return SandboxResult(exit_code=-1, cancelled=True, error="cancelado: porque sí")

    bus = SinkDeSkills()
    registry = SkillRegistry(tmp_path / "skills", sink=bus)
    registry.register(
        SkillManifest(id="echo_skill", description="Hace eco", entrypoint="main.py"),
        "print('{}')",
    )
    runner = SkillRunner(registry, Sandbox())
    token = CancelToken()
    token.request("porque sí")

    resultado = run(runner.run("echo_skill", {}, project_root=tmp_path, cancel=token))

    assert resultado.success is False
    assert "cancelado" in (resultado.error or "")
    emitidos = [evento.event_type for evento in bus.events]
    assert EventType.SKILL_FAILED not in emitidos
    assert EventType.SKILL_EXECUTED not in emitidos
