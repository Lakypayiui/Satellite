"""Aprobar un comando: la negativa deja de ser un callejón sin salida.

Espejo de la aprobación de rutas (mismo puerto, mismo modal, misma promesa: una vez /
la sesión / rechazar). Se prueba la lógica del runtime con una tool de mentira, así que
no se lanza ningún proceso de verdad.
"""

import asyncio
from pathlib import Path

from satellite.agents.base import AgentContext
from satellite.agents.runtime import LLMAgent
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.core.spec import AgentSpec
from satellite.core.tool_models import ToolCall, ToolResult
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import Tool, ToolContext, granted_for_this_call
from satellite.tools.registry import DefaultToolRegistry


def run(coro):
    return asyncio.run(coro)


class _PuertoGrabador:
    """Puerto de aprobación de mentira: graba lo que le piden y decide."""

    def __init__(self, *, approve: bool = True, feedback: str = "") -> None:
        self.approve = approve
        self.feedback = feedback
        self.calls: list = []

    async def request(self, proposal, ctx):
        self.calls.append(ctx)
        return ApprovalDecision(
            status=ApprovalStatus.APPROVED if self.approve else ApprovalStatus.REJECTED,
            feedback=self.feedback,
        )


class _ComandoFalso(Tool):
    """Copia mínima de ``run_command``: misma comprobación, sin lanzar nada."""

    def __init__(self) -> None:
        self.ejecutados: list[str] = []

    @property
    def id(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return "falso, para probar el permiso sin ejecutar procesos"

    @property
    def categories(self) -> tuple[str, ...]:
        return ("terminal",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        comando = str(arguments.get("command") or "")
        argv = comando.split()
        policy = tool_ctx.policy
        if (
            policy is not None
            and not policy.check_command(argv)
            and not granted_for_this_call(argv, tool_ctx)
        ):
            return ToolResult(
                success=False,
                error=f"permiso denegado: comando '{argv[0]}' no permitido",
                error_code="command_not_allowed",
                output={"command": comando, "binary": argv[0], "argv": argv},
            )
        self.ejecutados.append(comando)
        return ToolResult(
            success=True, output={"exit_code": 0, "stdout": "ok", "stderr": ""}
        )


def _agente(tmp_path: Path):
    """Runtime mínimo con el terminal habilitado, como lo construye el framework."""
    policy = DefaultSecurityPolicy(allowed_tools={"run_command"}, allow_terminal=True)
    registry = DefaultToolRegistry(policy=policy)
    tool = _ComandoFalso()
    registry.register(tool)
    agent = LLMAgent(
        spec=AgentSpec(
            id="runner",
            name="Runner",
            capabilities=("run",),
            prompt_ref="runner",
            tools=("run_command",),
        ),
        system_prompt="Eres un runner.\n{tools}",
        user_template="{task}",
        tools=registry.list_for(("run_command",)),
        provider=FakeLLM(script=[]),
        tool_registry=registry,
        policy=policy,
        project_root=tmp_path,
    )
    return agent, tool


def _llamada(comando: str) -> ToolCall:
    return ToolCall(id="c1", name="run_command", arguments={"command": comando})


def _por_el_runtime(agent, comando: str, ctx: AgentContext) -> ToolResult:
    """Recorre el camino real: negativa → permiso → reintento."""
    _record, result = run(agent._run_tool(_llamada(comando), ctx))
    return result


def test_the_allowlist_decides_once_the_terminal_is_enabled() -> None:
    """Con el terminal habilitado la allowlist sigue mandando; sin ella, se pregunta."""
    from satellite.cli.runtime_builder import DEFAULT_ALLOWED_TOOLS

    assert "run_argv" in DEFAULT_ALLOWED_TOOLS
    assert "run_shell" not in DEFAULT_ALLOWED_TOOLS

    abierto = DefaultSecurityPolicy(allowed_tools={"run_command"}, allow_terminal=True)
    # vacía = no pasa, pero con código: el runtime lo convierte en una petición
    assert abierto.check_command(["git", "status", "--short"]) is False
    # y lo concedido pasa
    abierto.allowed_commands = ("git",)
    assert abierto.check_command(["git", "status", "--short"]) is True

    # el veto duro sigue siendo un no seco, aunque la allowlist lo permita
    cerrado = DefaultSecurityPolicy(
        allowed_tools={"run_command"}, allowed_commands=("git",)
    )
    assert cerrado.check_command(["git", "status", "--short"]) is False


def test_a_command_is_asked_and_retried_with_permission(tmp_path: Path) -> None:
    """El comando se pregunta, se ve tal cual y, si se aprueba, se ejecuta."""
    agent, tool = _agente(tmp_path)
    port = _PuertoGrabador(approve=True, feedback="once")

    result = _por_el_runtime(
        agent, "git status --short", AgentContext(approval=port, project_root=tmp_path)
    )

    assert len(port.calls) == 1
    assert port.calls[0].kind == "command"
    assert port.calls[0].details["command"] == "git status --short"
    assert port.calls[0].details["binary"] == "git"
    assert port.calls[0].details["consequence"] == "ejecución"
    assert result.success is True
    assert tool.ejecutados == ["git status --short"]


def test_allowing_the_session_remembers_the_binary(tmp_path: Path) -> None:
    agent, _tool = _agente(tmp_path)
    port = _PuertoGrabador(approve=True, feedback="session")

    _por_el_runtime(agent, "git status", AgentContext(approval=port, project_root=tmp_path))

    assert agent.policy.allowed_commands == ("git",)
    assert agent.policy.check_command(["git", "status", "--short"]) is True


def test_the_one_off_grant_does_not_stick(tmp_path: Path) -> None:
    """«Una vez» no toca la política: la siguiente vuelve a preguntar."""
    agent, tool = _agente(tmp_path)
    port = _PuertoGrabador(approve=True, feedback="once")

    _por_el_runtime(agent, "git status", AgentContext(approval=port, project_root=tmp_path))

    assert tool.ejecutados == ["git status"]
    assert agent.policy.allowed_commands == ()
    assert agent.policy.check_command(["git", "status"]) is False


def test_a_rejected_command_keeps_the_honest_error(tmp_path: Path) -> None:
    agent, tool = _agente(tmp_path)
    port = _PuertoGrabador(approve=False)

    result = _por_el_runtime(
        agent, "rm -rf /tmp/x", AgentContext(approval=port, project_root=tmp_path)
    )

    assert len(port.calls) == 1
    assert result.success is False
    assert "no permitido" in (result.error or "")
    assert tool.ejecutados == []


def test_without_an_approval_port_the_command_stays_denied(tmp_path: Path) -> None:
    """Deny by default intacto: sin puerto no hay a quién preguntar."""
    agent, tool = _agente(tmp_path)

    result = _por_el_runtime(agent, "git status", AgentContext())

    assert result.success is False
    assert result.error_code == "command_not_allowed"
    assert tool.ejecutados == []
