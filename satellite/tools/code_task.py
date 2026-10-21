"""Bounded code/test runner for the language agents."""

from __future__ import annotations

import time
from pathlib import Path

from satellite.code.languages import (
    LANGUAGES,
    language_for_agent,
    language_for_path,
    normalize_language,
)
from satellite.code.profiles import build_plan
from satellite.code.summary import summarize
from satellite.context.languages import detect_project_languages
from satellite.core.tool_models import ToolResult
from satellite.sandbox.base import SandboxLevel, sanitize_env
from satellite.sandbox.levels import execution_sandbox
from satellite.tools.base import Tool, ToolContext
from satellite.tools.fs import _resolve
from satellite.tools.terminal import execute_argv


class CodeTaskTool(Tool):
    @property
    def id(self) -> str:
        return "code_task"

    @property
    def description(self) -> str:
        return (
            "Runs code or the project's tests with a fixed per-language profile "
            "(pytest, npm test, go test, cargo test, dotnet test, ...) and returns "
            "a parsed summary. It accepts no arbitrary command, shell, args or env. "
            "A failing test suite is a completed run with status='failed', not a "
            "tool error."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("code", "test")

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "test"],
                    "description": "Run a file or the project's test suite.",
                },
                "language": {
                    "type": "string",
                    "enum": sorted(LANGUAGES),
                    "description": "Language id; inferred from path or active agent.",
                },
                "path": {
                    "type": "string",
                    "description": "Project-relative file (run) or file/dir (test).",
                },
                "cwd": {
                    "type": "string",
                    "description": "Project-relative working directory.",
                },
                "filter": {
                    "type": "string",
                    "description": "Single test-name filter for the native runner.",
                },
                "timeout_s": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 900,
                    "description": "Total timeout in seconds (default 300).",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        action = arguments.get("action")
        if action not in ("run", "test"):
            return ToolResult(
                success=False,
                error="'action' debe ser 'run' o 'test'",
                error_code="invalid_arguments",
            )
        root = Path(tool_ctx.workdir or tool_ctx.project_root)
        path = None
        raw_path = arguments.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            try:
                path = _resolve(raw_path.strip(), tool_ctx)
            except Exception as exc:  # noqa: BLE001 — path outside/protected
                return ToolResult(
                    success=False,
                    error=str(exc),
                    error_code=getattr(exc, "code", "") or "invalid_path",
                    output=getattr(exc, "details", None),
                )
        cwd = root
        raw_cwd = arguments.get("cwd")
        if isinstance(raw_cwd, str) and raw_cwd.strip():
            try:
                cwd = _resolve(raw_cwd.strip(), tool_ctx)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    success=False,
                    error=str(exc),
                    error_code=getattr(exc, "code", "") or "invalid_path",
                    output=getattr(exc, "details", None),
                )
            if not cwd.is_dir():
                return ToolResult(
                    success=False,
                    error=f"'cwd' no es un directorio: {cwd}",
                    error_code="invalid_arguments",
                )

        language = _language(arguments, tool_ctx, path, root)
        if isinstance(language, ToolResult):
            return language
        filter_text = _filter(arguments)
        if isinstance(filter_text, ToolResult):
            return filter_text
        timeout_s = arguments.get("timeout_s", 300)
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, int)
            or timeout_s < 1
            or timeout_s > 900
        ):
            return ToolResult(
                success=False,
                error="'timeout_s' debe ser un entero entre 1 y 900",
                error_code="invalid_arguments",
            )

        sandbox, reason = execution_sandbox(tool_ctx)
        if reason is not None:
            return ToolResult(
                success=False,
                error=reason,
                error_code="sandbox_unavailable",
            )
        sandbox_n1 = (
            sandbox is not None
            and getattr(sandbox, "level", None) is SandboxLevel.N1
        )
        plan = build_plan(
            language,
            action,
            root=root,
            path=path,
            filter_text=filter_text,
            sandbox_n1=sandbox_n1,
        )
        if plan.status == "invalid":
            return ToolResult(
                success=False,
                error=plan.reason,
                error_code="invalid_arguments",
            )
        if plan.status == "unsupported":
            return ToolResult(
                success=False,
                error=plan.reason,
                error_code="code_unsupported",
                output={"hint": plan.hint, "language": language, "action": action},
            )
        if plan.status == "tool_missing":
            return ToolResult(
                success=False,
                error=plan.reason,
                error_code="tool_missing",
                output={"hint": plan.hint, "language": language, "action": action},
            )

        env = sanitize_env(tool_ctx.env)
        env["PYTHONUNBUFFERED"] = "1"
        started = time.monotonic()
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        commands = [step.display for step in plan.steps]
        exit_code = 0
        status = "passed"
        try:
            for index, step in enumerate(plan.steps):
                remaining = max(1, int(timeout_s - (time.monotonic() - started)))
                result = await execute_argv(
                    list(step.argv),
                    cwd=cwd,
                    env=env,
                    timeout_s=remaining,
                    cancel=getattr(tool_ctx, "cancel", None),
                    sandbox=sandbox,
                )
                output = result.output if isinstance(result.output, dict) else {}
                stdout_parts.append(str(output.get("stdout") or ""))
                stderr_parts.append(str(output.get("stderr") or ""))
                if output.get("exit_code") is not None:
                    try:
                        exit_code = int(output["exit_code"])
                    except (TypeError, ValueError):
                        exit_code = 1
                if result.error_code == "cancelled":
                    status = "cancelled"
                    break
                if result.error_code == "sandbox_unavailable":
                    return ToolResult(
                        success=False,
                        error=result.error or "sandbox no disponible",
                        error_code="sandbox_unavailable",
                        output={"commands": commands},
                    )
                if "timeout" in str(result.error or "").lower():
                    status = "timeout"
                    break
                if not result.success:
                    status = "failed"
                    break
        except Exception as exc:  # noqa: BLE001 — report, never crash the agent
            return ToolResult(
                success=False,
                error=f"no se pudo ejecutar: {exc}",
                error_code="code_failed",
                output={"commands": commands},
            )
        finally:
            for directory in plan.cleanup:
                try:
                    import shutil

                    shutil.rmtree(directory, ignore_errors=True)
                except OSError:
                    pass

        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        duration_ms = int((time.monotonic() - started) * 1000)
        summary = summarize(language, action, exit_code, stdout, stderr)
        terminal = status in ("timeout", "cancelled")
        if status == "passed" and exit_code != 0:
            status = "failed"
        return ToolResult(
            success=not terminal,
            error=(
                "timeout alcanzado" if status == "timeout" else
                "cancelado por el usuario" if status == "cancelled" else
                None
            ),
            error_code=status if terminal else "",
            output={
                "status": status,
                "language": language,
                "action": action,
                "commands": commands,
                "exit_code": exit_code,
                "summary": summary,
                "stdout_tail": stdout[-8000:],
                "stderr_tail": stderr[-8000:],
                "duration_ms": duration_ms,
            },
        )


def _language(
    arguments: dict,
    tool_ctx: ToolContext,
    path: Path | None,
    root: Path,
) -> str | ToolResult:
    raw = arguments.get("language")
    if raw:
        language = normalize_language(raw)
        if language is None:
            return ToolResult(
                success=False,
                error=f"lenguaje no soportado: {raw}",
                error_code="lsp_unsupported_language",
            )
        return language
    if path is not None:
        language = language_for_path(path)
        if language is not None:
            return language
    spec = getattr(tool_ctx, "active_spec", None)
    agent_id = getattr(spec, "id", "")
    if not agent_id and isinstance(spec, dict):
        agent_id = str(spec.get("id") or "")
    language = language_for_agent(agent_id)
    if language is not None:
        return language
    detected = detect_project_languages(root, max_files=1000)
    if detected:
        return detected[0].id
    return ToolResult(
        success=False,
        error="no se pudo determinar el lenguaje; usa 'language' o 'path'",
        error_code="language_required",
    )


def _filter(arguments: dict) -> str | None | ToolResult:
    raw = arguments.get("filter")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return ToolResult(
            success=False,
            error="'filter' debe ser un string",
            error_code="invalid_arguments",
        )
    value = raw.strip()
    if not value:
        return None
    if len(value) > 200 or any(ord(character) < 32 for character in value):
        return ToolResult(
            success=False,
            error="'filter' inválido",
            error_code="invalid_arguments",
        )
    return value
