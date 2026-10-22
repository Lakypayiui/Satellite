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


def _python_argv(code: str, sandbox) -> list[str]:
    """Intérprete para ``run_python``: el de la imagen en niveles de contenedor.

    En N1 el proceso corre dentro del contenedor (``python:3.12-slim``), donde ``python``
    existe; la ruta de ``sys.executable`` es la del host y no vale dentro. En N0/N2 (y
    sin sandbox) se usa el intérprete actual, como siempre.
    """
    from satellite.sandbox.base import SandboxLevel

    if sandbox is not None and getattr(sandbox, "level", None) is SandboxLevel.N1:
        return ["python", "-c", code]
    return [sys.executable, "-c", code]


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
            "Alias of run_command, with the same policy and the same engine: it "
            "does NOT open a shell (no pipes, no redirections, no &&, no shell "
            "variables)."
        )


class RunPythonTool(Tool):
    """Ejecuta código Python en un subproceso aislado (sin shell)."""

    @property
    def id(self) -> str:
        return "run_python"

    @property
    def description(self) -> str:
        return (
            "Runs Python code in an isolated subprocess (no shell) with a clean "
            "environment and captures stdout/stderr; the code can read and write "
            "whatever the system allows."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("terminal",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to run."},
                "timeout_s": {"type": "integer", "description": "Timeout in seconds (default 30)."},
            },
            "required": ["code"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        code = arguments.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(success=False, error="se requiere el argumento 'code'")
        timeout_s = int(arguments.get("timeout_s", 30))
        from satellite.sandbox.levels import execution_sandbox
        from satellite.tools.base import granted_for_this_call

        sandbox, motivo = execution_sandbox(tool_ctx)
        if motivo is not None:
            return ToolResult(
                success=False,
                error=motivo,
                error_code="sandbox_unavailable",
                output={"command": code},
            )
        argv = _python_argv(code, sandbox)
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
            sandbox=sandbox,
        )


class SearchFilesTool(Tool):
    """Busca archivos por patrón glob relativo al proyecto."""

    @property
    def id(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return "Finds files by glob pattern (e.g. '**/*.py') inside the project."

    @property
    def categories(self) -> tuple[str, ...]:
        return ("filesystem",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Relative glob pattern."},
                "path": {"type": "string", "description": "Base subdirectory (default '.')."},
            },
            "required": ["pattern"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        pattern = arguments.get("pattern", "")
        if not isinstance(pattern, str) or not pattern.strip():
            return ToolResult(success=False, error="se requiere el argumento 'pattern'")
        if "\x00" in pattern:
            return ToolResult(
                success=False,
                error="el patrón contiene un carácter nulo",
                error_code="invalid_arguments",
            )
        try:
            pattern_path = Path(pattern)
        except (TypeError, ValueError) as exc:
            return ToolResult(
                success=False,
                error=f"patrón inválido: {exc}",
                error_code="invalid_arguments",
            )
        # Los globs absolutos hacen que Path.glob lance NotImplementedError, y ``..``
        # permite listar fuera del proyecto aunque el glob se resuelva de forma léxica.
        if pattern_path.is_absolute() or pattern_path.drive or ".." in pattern_path.parts:
            return ToolResult(
                success=False,
                error="el patrón debe ser relativo al proyecto y no contener '..'",
                error_code="invalid_arguments",
            )

        base = tool_ctx.workdir or tool_ctx.project_root
        raw = arguments.get("path")
        root = (base / raw) if isinstance(raw, str) and raw else base
        policy = tool_ctx.policy
        try:
            # Normalizar antes de recorrer evita que ``path`` introduzca ``..`` en los
            # resultados; la política sigue comprobando la ruta resuelta.
            root = Path(root).resolve()
        except OSError as exc:
            return ToolResult(
                success=False,
                error=f"ruta inválida: {exc}",
                error_code="invalid_arguments",
            )
        if policy is not None and not policy.check_path(root, tool_ctx.project_root):
            return ToolResult(success=False, error=f"permiso denegado: {root}")
        if not root.is_dir():
            return ToolResult(success=False, error=f"no es un directorio: {root}")
        try:
            matches = sorted(root.glob(pattern))
        except (NotImplementedError, OSError, ValueError) as exc:
            return ToolResult(
                success=False,
                error=f"patrón inválido: {exc}",
                error_code="invalid_arguments",
            )

        project_root = Path(tool_ctx.project_root).resolve()
        files = []
        for match in matches:
            try:
                # Se resuelve para que un symlink no escape del proyecto, y se exige que
                # el destino siga estando dentro de la raíz del proyecto.
                resolved = match.resolve(strict=True)
                if not resolved.is_file():
                    continue
                resolved.relative_to(project_root)
                if policy is not None and not policy.check_path(resolved, project_root):
                    continue
                rel = match.relative_to(project_root).as_posix()
                size = resolved.stat().st_size
            except (OSError, ValueError, RuntimeError):
                continue
            files.append({"path": rel, "size": size})
        return ToolResult(success=True, output=files)
