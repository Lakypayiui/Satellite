"""Tool de terminal y helper de ejecución de argv en subproceso."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from satellite.core.tool_models import ToolResult
from satellite.sandbox.base import SandboxLevel, SandboxSpec, sanitize_env
from satellite.tools.base import Tool, ToolContext, inside_execution_roots

OUTPUT_LIMIT_CHARS = 64_000


def _truncate(text: str, limit: int = OUTPUT_LIMIT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncado: {len(text) - limit} caracteres omitidos]"


def _tool_result_from_sandbox(raw, argv: list[str], timeout_s: int) -> ToolResult:
    """Normaliza un ``SandboxResult`` a ``ToolResult`` sin ocultar el fallo cerrado.

    Un error interno del sandbox (p. ej. Docker no disponible) se marca con
    ``error_code="sandbox_unavailable"``: quien lo lea sabe que **no** se ejecutó en el
    host, en vez de confundirlo con un comando que falló por su cuenta.
    """
    output = {
        "exit_code": raw.exit_code,
        "stdout": raw.stdout,
        "stderr": raw.stderr,
    }
    if raw.timed_out:
        output["timed_out"] = True
        return ToolResult(
            success=False,
            error=raw.error or f"timeout de {timeout_s}s alcanzado",
            output=output,
        )
    if raw.cancelled:
        output["cancelled"] = True
        output["argv"] = list(argv)
        return ToolResult(
            success=False,
            error=raw.error or "cancelado por el usuario",
            error_code="cancelled",
            output=output,
        )
    if raw.error is not None:
        return ToolResult(
            success=False,
            error=raw.error,
            error_code="sandbox_unavailable",
            output=output,
        )
    return ToolResult(success=raw.exit_code == 0, output=output)


async def _run_in_sandbox(
    sandbox,
    argv: list[str],
    *,
    cwd: Path,
    env: dict,
    timeout_s: int,
    stdin: str | None,
    cancel,
) -> ToolResult:
    """Ejecuta ``argv`` por el sandbox configurado (N0/N1/N2), sin shell."""
    spec = SandboxSpec(
        project_root=Path(cwd),
        level=getattr(sandbox, "level", SandboxLevel.N0),
        workdir=Path(cwd),
        # El aislamiento del nivel manda: N1 corta la red (--network none). N0/N2 no
        # confinan la red de todas formas, así que el valor no cambia su comportamiento.
        allow_network=False,
        timeout_s=timeout_s,
        env=dict(env or {}),
        stdin=stdin,
        cancel=cancel,
    )
    raw = await sandbox.run(argv, spec)
    return _tool_result_from_sandbox(raw, argv, timeout_s)


async def execute_argv(
    argv: list[str],
    *,
    cwd: Path,
    env: dict,
    timeout_s: int,
    stdin: str | None = None,
    output_limit: int = OUTPUT_LIMIT_CHARS,
    cancel=None,
    sandbox=None,
) -> ToolResult:
    """Ejecuta un ``argv`` sin shell y devuelve un ``ToolResult`` normalizado.

    ``cancel`` es el token de la ejecución: si alguien pide cancelar mientras el proceso
    corre, se le **termina** (no se espera a que acabe, que es lo único que hace útil
    poder cancelar un comando largo).

    ``sandbox`` (``SandboxRunner``) es el aislamiento efectivo del nivel configurado: si
    se pasa, el proceso corre **por él** (N0 subproceso / N1 Docker / N2 host) y un
    backend no disponible devuelve un error en lugar de ejecutar en el host.
    """
    if sandbox is not None:
        return await _run_in_sandbox(
            sandbox,
            argv,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            stdin=stdin,
            cancel=cancel,
        )

    from satellite.sandbox.process import run_process

    try:
        outcome = await run_process(
            argv, cwd=cwd, env=env, timeout_s=timeout_s, stdin=stdin, cancel=cancel
        )
    except (OSError, ValueError) as exc:
        return ToolResult(success=False, error=f"no se pudo ejecutar: {exc}")

    if outcome.timed_out:
        return ToolResult(success=False, error=f"timeout de {timeout_s}s alcanzado")

    if outcome.cancelled:
        motivo = str(getattr(cancel, "reason", "") or "cancelado por el usuario")
        return ToolResult(
            success=False,
            error=f"cancelado: {motivo}",
            error_code="cancelled",
            output={"cancelled": True, "argv": list(argv)},
        )

    return ToolResult(
        success=outcome.exit_code == 0,
        output={
            "exit_code": outcome.exit_code,
            "stdout": _truncate(outcome.stdout.decode(errors="replace"), output_limit),
            "stderr": _truncate(outcome.stderr.decode(errors="replace"), output_limit),
        },
    )


@dataclass
class RunCommandTool(Tool):
    """Ejecuta un comando del sistema sin shell, con timeout y entorno limpio."""

    timeout_default: int = 30

    @property
    def id(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Runs a program with arguments (no shell): it does not interpret pipes, "
            "globs or environment variables; it captures stdout/stderr."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command and arguments as a string."},
                "timeout_s": {"type": "integer", "description": "Timeout in seconds (default 30)."},
                "cwd_relative": {
                    "type": "string",
                    "description": "Working directory relative to the project (optional).",
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "If true, the command keeps running in the background and returns a job_id "
                        "to query with the 'job' tool (default false)."
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        cmd_raw = arguments.get("command", "")
        if not isinstance(cmd_raw, str) or not cmd_raw.strip():
            return ToolResult(success=False, error="se requiere el argumento 'command'")
        raw_timeout = arguments.get("timeout_s", self.timeout_default)
        try:
            if isinstance(raw_timeout, bool):
                raise ValueError
            timeout_s = int(raw_timeout)
        except (TypeError, ValueError, OverflowError):
            return ToolResult(
                success=False,
                error="'timeout_s' debe ser un entero positivo",
                error_code="invalid_arguments",
            )
        if timeout_s <= 0:
            return ToolResult(
                success=False,
                error="'timeout_s' debe ser un entero positivo",
                error_code="invalid_arguments",
            )
        try:
            argv = shlex.split(cmd_raw)
        except ValueError as exc:
            return ToolResult(
                success=False,
                error=f"comando inválido: {exc}",
                error_code="invalid_arguments",
            )
        if not argv or not argv[0].strip():
            return ToolResult(
                success=False,
                error="se requiere el argumento 'command'",
                error_code="invalid_arguments",
            )
        requested = list(argv)  # what the user/model asked for (policy identity)
        from satellite.sandbox.levels import background_error, execution_sandbox
        from satellite.tools.base import granted_for_this_call

        policy = tool_ctx.policy
        if policy is not None and not policy.check_command(requested) and not granted_for_this_call(requested, tool_ctx):
            return ToolResult(
                success=False,
                error=f"permiso denegado: comando '{requested[0]}' no permitido",
                error_code="command_not_allowed",
                output={"command": cmd_raw, "binary": requested[0], "argv": list(requested)},
            )

        # When Satellite runs inside a venv, a bare `python` in a command should use
        # that venv (the same interpreter as the runtime), not whatever `python` the
        # OS PATH happens to expose. Policy still sees the requested `python`; only
        # the executed argv is rewritten. A Docker level keeps it as-is because the
        # host path does not exist inside the container.
        if (
            argv
            and sys.prefix != sys.base_prefix
            and Path(argv[0]).name.lower() in {"python", "python.exe", "python3", "python3.exe"}
        ):
            sandbox, _ = execution_sandbox(tool_ctx)
            if sandbox is None or getattr(sandbox, "level", None) is not SandboxLevel.N1:
                argv[0] = sys.executable
        base = tool_ctx.workdir or tool_ctx.project_root
        cwd = base
        rel = arguments.get("cwd_relative")
        if isinstance(rel, str) and rel:
            cwd = (base / rel).resolve()
            if not inside_execution_roots(policy, cwd, tool_ctx):
                return ToolResult(success=False, error=f"permiso denegado: {cwd}")
        env = sanitize_env(tool_ctx.env)
        sandbox, motivo = execution_sandbox(tool_ctx)
        if motivo is not None:
            return ToolResult(
                success=False,
                error=motivo,
                error_code="sandbox_unavailable",
                output={"command": cmd_raw, "binary": argv[0], "argv": list(argv)},
            )
        if bool(arguments.get("background", False)):
            segundo = background_error(tool_ctx)
            if segundo is not None:
                return ToolResult(success=False, error=segundo, error_code="sandbox_unavailable")
            from satellite.tools.jobs import start_background

            return start_background(
                cmd_raw, argv, cwd=cwd, env=env, project_root=tool_ctx.project_root
            )
        return await execute_argv(
            argv,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            cancel=getattr(tool_ctx, "cancel", None),
            sandbox=sandbox,
        )
