"""Primitivos base del runtime expuestos como tools del kernel.

Los primitivos son herramientas del framework (no skills de usuario) y sus ids
están reservados: no pueden crearse ni sobrescribirse.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from satellite.core.tool_models import ToolResult
from satellite.sandbox.base import sanitize_env
from satellite.tools.base import Tool, ToolContext
from satellite.tools.fs import ListDirTool, ReadFileTool, WriteFileTool
from satellite.tools.terminal import RunCommandTool, execute_argv

__all__ = [
    "RunPythonTool",
    "RunArgvTool",
    "SearchFilesTool",
    "ReadFileTool",
    "WriteFileTool",
    "ListDirTool",
]


@dataclass
class RunArgvTool(RunCommandTool):
    """Primitivo ``run_argv``: alias de ``run_command`` (misma política).

    El nombre es honesto: el comando llega como texto y se parte en argv, pero **no**
    se abre un shell, así que no hay tuberías, redirecciones, ``&&`` ni variables.
    """

    @property
    def id(self) -> str:
        return "run_argv"

    @property
    def description(self) -> str:
        return (
            "Alias de run_command, con la misma política y el mismo motor: "
            "NO abre un intérprete de shell (ni tuberías, ni redirecciones, "
            "ni &&, ni variables de shell)."
        )


class RunPythonTool(Tool):
    """Ejecuta código Python en un subproceso aislado (sin shell)."""

    @property
    def id(self) -> str:
        return "run_python"

    @property
    def description(self) -> str:
        return (
            "Ejecuta código Python en un subproceso aislado (sin shell) con entorno "
            "limpio y captura stdout/stderr; el código puede leer y escribir lo que "
            "permita el sistema."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("terminal",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Código Python a ejecutar."},
                "timeout_s": {"type": "integer", "description": "Timeout en segundos (default 30)."},
            },
            "required": ["code"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        code = arguments.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(success=False, error="se requiere el argumento 'code'")
        timeout_s = int(arguments.get("timeout_s", 30))
        argv = [sys.executable, "-c", code]
        from satellite.tools.base import granted_for_this_call

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
                output={"command": code, "binary": argv[0], "argv": list(argv)},
            )
        cwd = tool_ctx.workdir or tool_ctx.project_root
        env = sanitize_env(tool_ctx.env)
        return await execute_argv(
            argv,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            cancel=getattr(tool_ctx, "cancel", None),
        )


class SearchFilesTool(Tool):
    """Busca archivos por patrón glob relativo al proyecto."""

    @property
    def id(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return "Busca archivos por patrón glob (p. ej. '**/*.py') dentro del proyecto."

    @property
    def categories(self) -> tuple[str, ...]:
        return ("filesystem",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Patrón glob relativo."},
                "path": {"type": "string", "description": "Subdirectorio base (default '.')."},
            },
            "required": ["pattern"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        pattern = arguments.get("pattern", "")
        if not isinstance(pattern, str) or not pattern.strip():
            return ToolResult(success=False, error="se requiere el argumento 'pattern'")
        base = tool_ctx.workdir or tool_ctx.project_root
        raw = arguments.get("path")
        root = (base / raw) if isinstance(raw, str) and raw else base
        policy = tool_ctx.policy
        if policy is not None and not policy.check_path(root, tool_ctx.project_root):
            return ToolResult(success=False, error=f"permiso denegado: {root}")
        if not Path(root).is_dir():
            return ToolResult(success=False, error=f"no es un directorio: {root}")
        matches = sorted(Path(root).glob(pattern))
        files = []
        for match in matches:
            if not match.is_file():
                continue
            try:
                size = match.stat().st_size
                rel = match.relative_to(tool_ctx.project_root).as_posix()
            except (OSError, ValueError):
                continue
            files.append({"path": rel, "size": size})
        return ToolResult(success=True, output=files)
