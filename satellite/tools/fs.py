"""Tools de filesystem: read_file, write_file, edit_file, list_dir."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from satellite.core.errors import ToolError
from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext, inside_execution_roots

READ_LIMIT_CHARS = 50_000


class ReadFileTool(Tool):
    """Lee el contenido de un archivo de texto."""

    @property
    def id(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Reads the contents of a text file (UTF-8, up to ~50 KB). Pass start_line and "
            "end_line (1-based, inclusive) to read only a range of a large file."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the project."},
                "start_line": {"type": "integer", "description": "First line to read (1-based)."},
                "end_line": {"type": "integer", "description": "Last line to read (inclusive)."},
            },
            "required": ["path"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        path = _resolve(arguments.get("path"), tool_ctx)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, error=f"no se pudo leer '{path}': {exc}")
        start, end = _line_range(arguments)
        if start or end:
            lines = content.splitlines()
            total = len(lines)
            first = max(1, start or 1)
            last = min(total, end or total)
            if first > total:
                return ToolResult(
                    success=False, error=f"el archivo tiene {total} líneas; pediste desde la {first}"
                )
            numbered = "\n".join(
                f"{number}: {line}" for number, line in enumerate(lines[first - 1 : last], first)
            )
            content = f"[líneas {first}-{last} de {total}]\n{numbered}"
        truncated = len(content) > READ_LIMIT_CHARS
        if truncated:
            content = content[:READ_LIMIT_CHARS]
            note = f"\n\n[truncado: el archivo excede {READ_LIMIT_CHARS} caracteres]"
            content += note
        return ToolResult(success=True, output=content)


def _line_range(arguments: dict) -> tuple[int, int]:
    """``start_line``/``end_line`` válidos (0 = sin límite por ese lado)."""
    values = []
    for key in ("start_line", "end_line"):
        try:
            value = int(arguments.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        values.append(max(0, value))
    return values[0], values[1]


class WriteFileTool(Tool):
    """Escribe contenido en un archivo, creando directorios padre."""

    @property
    def id(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Writes content to a file (UTF-8), creating parent directories if missing."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to write, relative to the project."},
                "content": {"type": "string", "description": "Content to write."},
            },
            "required": ["path", "content"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        path = _resolve(arguments.get("path"), tool_ctx)
        content = arguments.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        anterior = _read_previous(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            path.write_bytes(data)
        except OSError as exc:
            return ToolResult(success=False, error=f"no se pudo escribir '{path}': {exc}")
        output = {"path": str(path), "bytes": len(data)}
        if not _record_change(tool_ctx, path, anterior):
            output["aviso"] = "no se pudo registrar el cambio para poder deshacerlo"
        return ToolResult(success=True, output=output)


class EditFileTool(Tool):
    """Reemplaza la PRIMERA ocurrencia exacta de old_string por new_string."""

    @property
    def id(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Replaces the first exact occurrence of a text in a file."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to edit, relative to the project."},
                "old_string": {"type": "string", "description": "Text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        path = _resolve(arguments.get("path"), tool_ctx)
        old = arguments.get("old_string", "")
        new = arguments.get("new_string", "")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, error=f"no se pudo leer '{path}': {exc}")
        if old not in content:
            return ToolResult(
                success=False,
                error=f"old_string no encontrado en '{path}'",
            )
        new_content = content.replace(old, new, 1)
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, error=f"no se pudo escribir '{path}': {exc}")
        output = {"path": str(path), "reemplazos": 1}
        if not _record_change(tool_ctx, path, content):
            output["aviso"] = "no se pudo registrar el cambio para poder deshacerlo"
        return ToolResult(success=True, output=output)


class ListDirTool(Tool):
    """Lista entradas de un directorio (sin recursión por defecto)."""

    @property
    def id(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "Lists directory entries with name, type, size and mtime."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the directory to list, relative to the project."},
                "recursive": {"type": "boolean", "description": "Whether to list recursively (default false)."},
            },
            "required": ["path"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        path = _resolve(arguments.get("path"), tool_ctx)
        recursive = bool(arguments.get("recursive", False))
        if not path.exists():
            return ToolResult(success=False, error=f"no existe '{path}'")
        if not path.is_dir():
            return ToolResult(success=False, error=f"'{path}' no es un directorio")
        try:
            entries = _list_entries(path, recursive)
        except OSError as exc:
            return ToolResult(success=False, error=f"no se pudo listar '{path}': {exc}")
        return ToolResult(success=True, output=entries)


def _list_entries(path: Path, recursive: bool) -> list[dict]:
    entries: list[dict] = []
    stack = [path]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir(), key=lambda p: p.name):
            entry = {
                "path": str(child),
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
                "mtime": datetime.fromtimestamp(child.stat().st_mtime).isoformat(),
            }
            entries.append(entry)
            if recursive and child.is_dir():
                stack.append(child)
    return entries


def _resolve(raw: object, tool_ctx: ToolContext) -> Path:
    """Resuelve la ruta pedida dentro del proyecto (via política, concesiones y zonas).

    Lanza ``PathOutsideProject`` si queda fuera del proyecto, que el runtime puede
    convertir en una petición de permiso, y ``ProtectedZoneForbidden`` si toca una
    zona interna del framework, que no se concede nunca (ni leyendo).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("se requiere el argumento 'path' (string no vacío)")
    base = tool_ctx.workdir or tool_ctx.project_root
    # `~` se expande de verdad: si no, "~/Desktop/x" quedaba como
    # "<proyecto>/~/Desktop/x" y el error decía "no existe", que es una pista falsa.
    candidata = Path(raw).expanduser()
    path = candidata if candidata.is_absolute() else base / candidata
    policy = tool_ctx.policy
    if (
        policy is not None
        and not inside_execution_roots(policy, path, tool_ctx)
        and not _granted_for_this_call(path, tool_ctx)
    ):
        from satellite.core.errors import PathOutsideProject

        raise PathOutsideProject(
            path.resolve(), Path(tool_ctx.project_root).resolve(), str(raw)
        )
    _assert_not_protected(path, tool_ctx, base)
    return path


def _granted_for_this_call(path: Path, tool_ctx: ToolContext) -> bool:
    """True si una aprobación puntual concedió esta ruta para esta llamada."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for allowed in getattr(tool_ctx, "allowed_paths", None) or ():
        base = Path(allowed).resolve()
        if resolved == base or base in resolved.parents:
            return True
    return False


def _assert_not_protected(path: Path, tool_ctx: ToolContext, base: Path) -> None:
    """Veta las zonas internas del framework (la ruta se resuelve antes de mirar)."""
    from satellite.core.errors import ProtectedZoneForbidden
    from satellite.kernel.guard import protected_zone
    from satellite.kernel.paths import resolve_paths

    paths = getattr(tool_ctx, "paths", None)
    if paths is None:
        try:
            paths = resolve_paths(tool_ctx.project_root)
        except Exception:  # noqa: BLE001 — sin rutas no se puede afirmar nada
            return
    zone = protected_zone(path, paths)
    if zone:
        raise ProtectedZoneForbidden(f"zona protegida ({zone}): no se puede tocar {path}")


def _read_previous(path: Path) -> str | None:
    """Contenido previo del archivo (None si no existía o no se pudo leer)."""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return None


def _record_change(tool_ctx: ToolContext, path: Path, previous: str | None) -> bool:
    """Anota el cambio para poder mostrarlo y deshacerlo después."""
    from satellite.persistence.changes import ChangeLedger

    task_id = str(getattr(tool_ctx, "task_id", "") or "")
    if not task_id or tool_ctx.project_root is None:
        return True  # sin tarea no hay a quién atribuirlo: no es un fallo
    try:
        return ChangeLedger(tool_ctx.project_root).record(
            task_id, path, previous, workdir=getattr(tool_ctx, "workdir", None)
        )
    except OSError:
        return False
