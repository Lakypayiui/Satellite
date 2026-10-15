"""Tools de filesystem: read_file, write_file, edit_file, list_dir."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from satellite.core.errors import ToolError
from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext

READ_LIMIT_CHARS = 50_000


class ReadFileTool(Tool):
    """Lee el contenido de un archivo de texto."""

    @property
    def id(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Lee el contenido de un archivo de texto (UTF-8, hasta ~50 KB)."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del archivo, relativa al proyecto."}
            },
            "required": ["path"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        path = _resolve(arguments.get("path"), tool_ctx)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, error=f"no se pudo leer '{path}': {exc}")
        truncated = len(content) > READ_LIMIT_CHARS
        if truncated:
            content = content[:READ_LIMIT_CHARS]
            note = f"\n\n[truncado: el archivo excede {READ_LIMIT_CHARS} caracteres]"
            content += note
        return ToolResult(success=True, output=content)


class WriteFileTool(Tool):
    """Escribe contenido en un archivo, creando directorios padre."""

    @property
    def id(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Escribe contenido en un archivo (UTF-8), creando directorios padre si faltan."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del archivo a escribir."},
                "content": {"type": "string", "description": "Contenido a escribir."},
            },
            "required": ["path", "content"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        path = _resolve(arguments.get("path"), tool_ctx)
        content = arguments.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            path.write_bytes(data)
        except OSError as exc:
            return ToolResult(success=False, error=f"no se pudo escribir '{path}': {exc}")
        return ToolResult(success=True, output={"path": str(path), "bytes": len(data)})


class EditFileTool(Tool):
    """Reemplaza la PRIMERA ocurrencia exacta de old_string por new_string."""

    @property
    def id(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Reemplaza la primera aparición exacta de un texto en un archivo."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del archivo a editar."},
                "old_string": {"type": "string", "description": "Texto a reemplazar."},
                "new_string": {"type": "string", "description": "Texto de reemplazo."},
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
        return ToolResult(success=True, output={"path": str(path), "reemplazos": 1})


class ListDirTool(Tool):
    """Lista entradas de un directorio (sin recursión por defecto)."""

    @property
    def id(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "Lista entradas de un directorio con nombre, tipo, tamaño y mtime."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del directorio a listar."},
                "recursive": {"type": "boolean", "description": "Si listar recursivamente (default false)."},
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
    """Resuelve la ruta pedida dentro del proyecto (via política).

    Lanza ToolError si la ruta queda fuera de las raíces permitidas.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("se requiere el argumento 'path' (string no vacío)")
    base = tool_ctx.workdir or tool_ctx.project_root
    path = Path(raw) if Path(raw).is_absolute() else base / Path(raw)
    policy = tool_ctx.policy
    if policy is not None and not policy.check_path(path, tool_ctx.project_root):
        raise ToolError(f"permiso denegado: {path}")
    return path
