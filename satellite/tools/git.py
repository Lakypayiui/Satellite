"""GitStatusTool: informa del estado de git bajo la política vigente."""

from __future__ import annotations

from dataclasses import dataclass

from satellite.core.tool_models import ToolResult
from satellite.sandbox.base import sanitize_env
from satellite.tools.base import Tool, ToolContext
from satellite.tools.terminal import execute_argv


@dataclass
class GitStatusTool(Tool):
    """Ejecuta ``git status --short`` y ``git diff --stat`` (sin bloquear el loop)."""

    @property
    def id(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return "Muestra el estado del repositorio git (status --short y diff --stat)."

    @property
    def categories(self) -> tuple[str, ...]:
        return ("git",)

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        policy = tool_ctx.policy
        for argv in (["git", "status", "--short"], ["git", "diff", "--stat"]):
            if policy is not None and not policy.check_command(argv):
                return ToolResult(
                    success=False,
                    error=f"permiso denegado: comando '{argv[0]}' no permitido",
                )

        cwd = tool_ctx.workdir or tool_ctx.project_root
        env = sanitize_env(tool_ctx.env)
        status = await execute_argv(["git", "status", "--short"], cwd=cwd, env=env, timeout_s=30)
        diff = await execute_argv(["git", "diff", "--stat"], cwd=cwd, env=env, timeout_s=30)

        if not status.success:
            stderr = (status.output or {}).get("stderr", "") if status.output else ""
            return ToolResult(success=False, error=stderr.strip() or "no es un repo git")

        status_out = (status.output or {}).get("stdout", "") if status.output else ""
        diff_out = (diff.output or {}).get("stdout", "") if diff.output else ""
        return ToolResult(
            success=True,
            output={
                "status": status_out.strip() or "(sin cambios)",
                "diff_stat": diff_out.strip() or "(sin diff)",
            },
        )
