"""Tool de terminal y helper de ejecución de argv en subproceso."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path

from satellite.core.tool_models import ToolResult
from satellite.sandbox.base import sanitize_env
from satellite.tools.base import Tool, ToolContext

OUTPUT_LIMIT_CHARS = 64_000


def _truncate(text: str, limit: int = OUTPUT_LIMIT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncado: {len(text) - limit} caracteres omitidos]"


async def execute_argv(
    argv: list[str],
    *,
    cwd: Path,
    env: dict,
    timeout_s: int,
    stdin: str | None = None,
    output_limit: int = OUTPUT_LIMIT_CHARS,
) -> ToolResult:
    """Ejecuta un ``argv`` sin shell y devuelve un ``ToolResult`` normalizado."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        return ToolResult(success=False, error=f"no se pudo ejecutar: {exc}")

    payload = stdin.encode("utf-8") if stdin is not None else None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=payload), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ToolResult(success=False, error=f"timeout de {timeout_s}s alcanzado")

    return ToolResult(
        success=proc.returncode == 0,
        output={
            "exit_code": proc.returncode,
            "stdout": _truncate(stdout.decode(errors="replace"), output_limit),
            "stderr": _truncate(stderr.decode(errors="replace"), output_limit),
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
            "Ejecuta un programa con argumentos (sin shell): no interpreta tuberías, "
            "comodines ni variables de entorno; captura stdout/stderr."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando y argumentos como string."},
                "timeout_s": {"type": "integer", "description": "Timeout en segundos (default 30)."},
                "cwd_relative": {
                    "type": "string",
                    "description": "Directorio de trabajo relativo al proyecto (opcional).",
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "Si true, el comando sigue en segundo plano y devuelve un job_id "
                        "para consultarlo con la tool 'job' (default false)."
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        cmd_raw = arguments.get("command", "")
        timeout_s = int(arguments.get("timeout_s", self.timeout_default))
        if not isinstance(cmd_raw, str) or not cmd_raw.strip():
            return ToolResult(success=False, error="se requiere el argumento 'command'")
        argv = shlex.split(cmd_raw)
        from satellite.tools.base import granted_for_this_call

        policy = tool_ctx.policy
        if policy is not None and not policy.check_command(argv) and not granted_for_this_call(argv, tool_ctx):
            return ToolResult(
                success=False,
                error=f"permiso denegado: comando '{argv[0]}' no permitido",
                error_code="command_not_allowed",
                output={"command": cmd_raw, "binary": argv[0], "argv": list(argv)},
            )
        base = tool_ctx.workdir or tool_ctx.project_root
        cwd = base
        rel = arguments.get("cwd_relative")
        if isinstance(rel, str) and rel:
            cwd = (base / rel).resolve()
            if policy is not None and not policy.check_path(cwd, tool_ctx.project_root):
                return ToolResult(success=False, error=f"permiso denegado: {cwd}")
        env = sanitize_env(tool_ctx.env)
        if bool(arguments.get("background", False)):
            from satellite.tools.jobs import start_background

            return start_background(
                cmd_raw, argv, cwd=cwd, env=env, project_root=tool_ctx.project_root
            )
        return await execute_argv(argv, cwd=cwd, env=env, timeout_s=timeout_s)
