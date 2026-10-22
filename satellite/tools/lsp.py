"""Read-only LSP tool: diagnostics and code navigation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from satellite.code.languages import (
    LANGUAGES,
    language_for_agent,
    language_for_path,
    normalize_language,
)
from satellite.core.tool_models import ToolResult
from satellite.lsp.client import LspError
from satellite.lsp.manager import manager_for
from satellite.lsp.servers import server_for_language
from satellite.lsp.uris import from_lsp_range, uri_to_path
from satellite.tools.base import Tool, ToolContext
from satellite.tools.fs import _resolve

_ACTIONS = {
    "status",
    "install",
    "diagnostics",
    "definition",
    "references",
    "hover",
    "document_symbols",
    "workspace_symbols",
    "restart",
}

_SEVERITY = {1: "error", 2: "warning", 3: "information", 4: "hint"}

_SYMBOL_KINDS = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _severity(value: object) -> str:
    """Severidad LSP tolerante: valores raros se reportan como información."""
    if isinstance(value, bool) or not isinstance(value, int):
        return "information"
    return _SEVERITY.get(value, "information")


def _diagnostic(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    position = from_lsp_range(raw.get("range"))
    code = raw.get("code")
    return {
        **position,
        "severity": _severity(raw.get("severity")),
        "code": str(code) if code is not None else "",
        "source": str(raw.get("source") or ""),
        "message": str(raw.get("message") or "")[:2000],
    }


def _location(raw: Any, project_root: Path) -> dict | None:
    if not isinstance(raw, dict):
        return None
    uri = raw.get("uri") or raw.get("targetUri")
    if not isinstance(uri, str) or not uri:
        return None
    raw_range = (
        raw.get("targetSelectionRange")
        or raw.get("targetRange")
        or raw.get("range")
        or {}
    )
    path = uri_to_path(uri)
    result = {
        "uri": uri,
        "path": str(path) if path is not None else "",
        **from_lsp_range(raw_range),
        "external": bool(path is not None and not _inside(path, project_root)),
    }
    return result


def _hover_text(contents: Any) -> str:
    if isinstance(contents, str):
        return contents[:8000]
    if isinstance(contents, dict):
        value = contents.get("value")
        if isinstance(value, str):
            language = contents.get("language")
            if language:
                return f"```{language}\n{value}\n```"[:8000]
            return value[:8000]
        return str(contents)[:8000]
    if isinstance(contents, list):
        parts = [_hover_text(item) for item in contents]
        return "\n\n".join(part for part in parts if part)[:8000]
    return str(contents)[:8000] if contents is not None else ""


def _symbol_kind(value: object) -> str:
    """Tipo de símbolo LSP legible; números desconocidos se conservan como texto."""
    if isinstance(value, bool) or not isinstance(value, int):
        return str(value or "")
    return _SYMBOL_KINDS.get(value, str(value))


def _symbol(raw: Any, project_root: Path, *, max_items: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "")
    if not name:
        return None
    kind = _symbol_kind(raw.get("kind"))
    location = raw.get("location")
    if isinstance(location, dict):
        located = _location(location, project_root) or {}
        return {
            "name": name,
            "kind": kind,
            "path": located.get("path", ""),
            "line": located.get("line", 1),
            "column": located.get("column", 1),
            "container": str(raw.get("containerName") or ""),
        }
    result = {
        "name": name,
        "detail": str(raw.get("detail") or "")[:500],
        "kind": kind,
        **from_lsp_range(raw.get("range")),
        "children": [],
    }
    children = raw.get("children")
    if isinstance(children, list) and max_items > 0:
        for child in children[:max_items]:
            normalized = _symbol(child, project_root, max_items=max_items - 1)
            if normalized is not None:
                result["children"].append(normalized)
    return result


class LspTool(Tool):
    @property
    def id(self) -> str:
        return "lsp"

    @property
    def description(self) -> str:
        return (
            "Language intelligence through a real language server: diagnostics, "
            "definition, references, hover and document/workspace symbols. "
            "Read-only. Positions are 1-based. It uses the project's language "
            "server and can auto-install it locally under ~/.satellite/lsp."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("lsp", "code")

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(_ACTIONS),
                    "description": "Operation to perform.",
                },
                "language": {
                    "type": "string",
                    "enum": sorted(LANGUAGES),
                    "description": "Language id; inferred from path when omitted.",
                },
                "path": {
                    "type": "string",
                    "description": "Project-relative file path.",
                },
                "line": {"type": "integer", "minimum": 1, "description": "1-based line."},
                "column": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "1-based column.",
                },
                "query": {
                    "type": "string",
                    "description": "Workspace symbol query.",
                },
                "include_declaration": {
                    "type": "boolean",
                    "description": "Include the declaration in references (default true).",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum items to return (default 100).",
                },
                "wait_s": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 15,
                    "description": "Seconds to wait for pushed diagnostics (default 3).",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        action = arguments.get("action")
        if action not in _ACTIONS:
            return ToolResult(
                success=False,
                error=f"action inválida: {action!r}",
                error_code="invalid_arguments",
            )
        root = Path(tool_ctx.workdir or tool_ctx.project_root)
        manager = manager_for(root)

        if action == "status":
            return ToolResult(success=True, output=manager.status())

        if action == "install":
            language = arguments.get("language")
            normalized = normalize_language(language) if language else None
            if language and normalized is None:
                return ToolResult(
                    success=False,
                    error=f"lenguaje no soportado: {language}",
                    error_code="lsp_unsupported_language",
                )
            try:
                results = await manager.install(normalized)
            except LspError as exc:
                return ToolResult(
                    success=False,
                    error=str(exc),
                    error_code=exc.code,
                    output=exc.details,
                )
            failed = [item for item in results if not item.get("ok")]
            return ToolResult(
                success=not failed,
                error=("falló la instalación de: "
                       + ", ".join(item["id"] for item in failed))
                if failed
                else None,
                error_code="lsp_install_failed" if failed else "",
                output={"installed": results},
            )

        if action == "restart":
            language, error = _require_language(arguments, tool_ctx)
            if error is not None:
                return error
            if language is None:
                return ToolResult(
                    success=False,
                    error="'language' es obligatorio para restart",
                    error_code="language_required",
                )
            try:
                ok = await manager.restart(language)
            except LspError as exc:
                return ToolResult(
                    success=False, error=str(exc), error_code=exc.code
                )
            return ToolResult(
                success=ok,
                error=None if ok else "no se pudo reiniciar el servidor",
                error_code="" if ok else "lsp_unsupported_language",
                output={"language": language, "restarted": ok},
            )

        path = None
        if action != "workspace_symbols":
            raw_path = arguments.get("path")
            try:
                path = _resolve(raw_path, tool_ctx)
            except Exception as exc:  # noqa: BLE001 — path outside/protected
                return ToolResult(
                    success=False,
                    error=str(exc),
                    error_code=getattr(exc, "code", "") or "invalid_path",
                    output=getattr(exc, "details", None),
                )

        language, error = _require_language(arguments, tool_ctx, path=path)
        if error is not None:
            return error
        if language is None:
            return ToolResult(
                success=False,
                error="no se pudo determinar el lenguaje; usa 'language'",
                error_code="language_required",
            )

        if action == "workspace_symbols":
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                return ToolResult(
                    success=False,
                    error="'query' es obligatorio para workspace_symbols",
                    error_code="invalid_arguments",
                )
        max_results = _int_arg(arguments, "max_results", 100, 1, 500)
        if max_results is None:
            return ToolResult(
                success=False,
                error="'max_results' debe ser un entero entre 1 y 500",
                error_code="invalid_arguments",
            )

        try:
            client = await _race(
                tool_ctx, manager.ensure(language, file_path=path)
            )
            language_id = LANGUAGES[language].lsp_language_id
            server = server_for_language(language)
            base = {
                "language": language,
                "server": server.name if server else "",
                "root": str(root),
            }
            if action == "diagnostics":
                wait_s = _float_arg(arguments, "wait_s", 3.0, 0.0, 15.0)
                if wait_s is None:
                    return _invalid("'wait_s' debe ser un número entre 0 y 15")
                items, pending = await _race(
                    tool_ctx,
                    client.diagnostics(
                        path, language_id=language_id, wait_s=wait_s
                    ),
                )
                diagnostics = [
                    item for item in (_diagnostic(raw) for raw in items) if item
                ][:200]
                return ToolResult(
                    success=True,
                    output={
                        **base,
                        "path": str(path),
                        "pending": pending,
                        "count": len(diagnostics),
                        "diagnostics": diagnostics,
                    },
                )
            if action in ("definition", "references", "hover"):
                line, column = _position(arguments)
                if line is None:
                    return _invalid("'line' y 'column' deben ser enteros >= 1")
                if action == "definition":
                    raw = await _race(
                        tool_ctx,
                        client.definition(
                            path, line, column, language_id=language_id
                        ),
                    )
                    items = raw if isinstance(raw, list) else [raw]
                    locations = [
                        item for item in (_location(value, root) for value in items) if item
                    ][:max_results]
                    return ToolResult(
                        success=True,
                        output={
                            **base,
                            "path": str(path),
                            "line": line,
                            "column": column,
                            "count": len(locations),
                            "locations": locations,
                        },
                    )
                if action == "references":
                    raw = await _race(
                        tool_ctx,
                        client.references(
                            path,
                            line,
                            column,
                            include_declaration=bool(
                                arguments.get("include_declaration", True)
                            ),
                            language_id=language_id,
                        ),
                    )
                    items = raw if isinstance(raw, list) else []
                    locations = [
                        item for item in (_location(value, root) for value in items) if item
                    ][:max_results]
                    return ToolResult(
                        success=True,
                        output={
                            **base,
                            "path": str(path),
                            "line": line,
                            "column": column,
                            "count": len(locations),
                            "locations": locations,
                        },
                    )
                raw = await _race(
                    tool_ctx,
                    client.hover(path, line, column, language_id=language_id),
                )
                contents = raw.get("contents") if isinstance(raw, dict) else raw
                return ToolResult(
                    success=True,
                    output={
                        **base,
                        "path": str(path),
                        "line": line,
                        "column": column,
                        "text": _hover_text(contents),
                    },
                )
            if action == "document_symbols":
                raw = await _race(
                    tool_ctx,
                    client.document_symbols(path, language_id=language_id),
                )
                items = raw if isinstance(raw, list) else []
                symbols = [
                    item
                    for item in (
                        _symbol(value, root, max_items=max_results)
                        for value in items[:max_results]
                    )
                    if item
                ]
                return ToolResult(
                    success=True,
                    output={
                        **base,
                        "path": str(path),
                        "count": len(symbols),
                        "symbols": symbols,
                    },
                )
            if action == "workspace_symbols":
                raw = await _race(
                    tool_ctx, client.workspace_symbols(str(arguments["query"]))
                )
                items = raw if isinstance(raw, list) else []
                symbols = [
                    item
                    for item in (
                        _symbol(value, root, max_items=0)
                        for value in items[:max_results]
                    )
                    if item
                ]
                return ToolResult(
                    success=True,
                    output={
                        **base,
                        "query": arguments["query"],
                        "count": len(symbols),
                        "symbols": symbols,
                    },
                )
        except LspError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                error_code=exc.code,
                output=exc.details,
            )
        except Exception as exc:  # noqa: BLE001 — never crash the agent
            return ToolResult(
                success=False,
                error=f"fallo del LSP: {exc}",
                error_code="lsp_protocol_error",
            )
        return ToolResult(
            success=False,
            error=f"action no manejada: {action}",
            error_code="invalid_arguments",
        )


def _require_language(
    arguments: dict, tool_ctx: ToolContext, path: Path | None = None
) -> tuple[str | None, ToolResult | None]:
    raw = arguments.get("language")
    if raw:
        language = normalize_language(raw)
        if language is None:
            return None, ToolResult(
                success=False,
                error=f"lenguaje no soportado: {raw}",
                error_code="lsp_unsupported_language",
            )
        return language, None
    if path is not None:
        language = language_for_path(path)
        if language is not None:
            return language, None
    spec = getattr(tool_ctx, "active_spec", None)
    language = language_for_agent(getattr(spec, "id", ""))
    if language is not None:
        return language, None
    return None, None


def _int_arg(
    arguments: dict, name: str, default: int, minimum: int, maximum: int
) -> int | None:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _float_arg(
    arguments: dict, name: str, default: float, minimum: float, maximum: float
) -> float | None:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value < minimum or value > maximum:
        return None
    return value


def _position(arguments: dict) -> tuple[int | None, int | None]:
    line = arguments.get("line")
    column = arguments.get("column", 1)
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        return None, None
    if isinstance(column, bool) or not isinstance(column, int) or column < 1:
        return None, None
    return line, column


def _invalid(message: str) -> ToolResult:
    return ToolResult(
        success=False, error=message, error_code="invalid_arguments"
    )


async def _race(tool_ctx: ToolContext, awaitable: Any) -> Any:
    # Imported lazily: orchestration imports the agent runtime, which imports the
    # tool package; a module-level import would create a cycle.
    from satellite.orchestration.cancel import race_cancel

    value, cancelled = await race_cancel(
        getattr(tool_ctx, "cancel", None), awaitable
    )
    if cancelled:
        raise LspError("cancelled", "cancelado por el usuario")
    return value
