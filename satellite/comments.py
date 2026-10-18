"""Comentarios inline anclados a líneas del diff, persistidos por proyecto.

Viven en ``<proyecto>/.satellite/state/comments.json`` (atómicos, sin secretos) y sobreviven
recargas. Cada comentario ata: archivo, rango de líneas del diff (``line_start``/``line_end``),
texto y —cuando se conoce— la sesión y la tarea. La línea es la del diff **unificado** (la que
el usuario selecciona en el panel), no la del archivo final: es lo que permite anclarlo a lo que
se está revisando.

No borra nada por su cuenta y no resuelve automáticamente: escribir, resolver y reabrir son
operaciones explícitas.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

COMMENTS_FILE = Path(".satellite") / "state" / "comments.json"
MAX_TEXT_CHARS = 4000


class CommentError(ValueError):
    """Dato de comentario inválido (lo traduce el backend a un 4xx)."""


def _path(root: Path | str) -> Path:
    return Path(root).resolve() / COMMENTS_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(root: Path | str) -> list[dict]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _save(root: Path | str, items: list[dict]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _entero(valor: object, default: int = 0) -> int:
    try:
        return int(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalize(item: dict) -> dict:
    return {
        "id": str(item.get("id", "")),
        "file": str(item.get("file", "")),
        "side": str(item.get("side", "new")),
        "line_start": _entero(item.get("line_start", 0)),
        "line_end": _entero(item.get("line_end", 0)),
        "text": str(item.get("text", "")),
        "session_id": str(item.get("session_id", "")),
        "task_id": str(item.get("task_id", "")),
        "author": str(item.get("author", "")),
        "created_at": str(item.get("created_at", "")),
        "resolved": bool(item.get("resolved", False)),
        "resolved_at": str(item.get("resolved_at", "")),
    }


def list_comments(
    root: Path | str,
    *,
    file: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    include_resolved: bool = True,
) -> list[dict]:
    """Comentarios del proyecto, opcionalmente filtrados; los más recientes al final."""
    items = [_normalize(item) for item in _load(root)]
    if file is not None:
        items = [item for item in items if item["file"] == file]
    if task_id:
        items = [item for item in items if item["task_id"] == task_id]
    if session_id:
        items = [item for item in items if item["session_id"] == session_id]
    if not include_resolved:
        items = [item for item in items if not item["resolved"]]
    return items


def add_comment(
    root: Path | str,
    *,
    file: str,
    line_start: int,
    line_end: int | None = None,
    text: str,
    side: str = "new",
    session_id: str = "",
    task_id: str = "",
    author: str = "",
) -> dict:
    """Añade un comentario anclado a un rango de líneas del diff."""
    archivo = (file or "").strip()
    if not archivo:
        raise CommentError("falta el archivo del comentario")
    if Path(archivo).is_absolute() or ".." in Path(archivo).parts:
        raise CommentError("la ruta del archivo debe ser relativa y sin '..'")
    try:
        inicio = int(line_start)
        fin = int(line_end if line_end is not None else line_start)
    except (TypeError, ValueError) as exc:
        raise CommentError("las líneas deben ser números enteros") from exc
    if inicio < 1 or fin < 1:
        raise CommentError("las líneas empiezan en 1")
    if fin < inicio:
        raise CommentError("el rango de líneas está invertido")
    cuerpo = (text or "").strip()
    if not cuerpo:
        raise CommentError("el comentario no puede estar vacío")
    if len(cuerpo) > MAX_TEXT_CHARS:
        raise CommentError(f"el comentario supera {MAX_TEXT_CHARS} caracteres")
    lado = (side or "new").strip().lower()
    if lado not in ("new", "old"):
        raise CommentError("side debe ser 'new' u 'old'")

    item = {
        "id": uuid4().hex[:12],
        "file": archivo,
        "side": lado,
        "line_start": inicio,
        "line_end": fin,
        "text": cuerpo,
        "session_id": session_id.strip(),
        "task_id": task_id.strip(),
        "author": author.strip(),
        "created_at": _now(),
        "resolved": False,
        "resolved_at": "",
    }
    items = _load(root)
    items.append(item)
    _save(root, items)
    return _normalize(item)


def update_comment(
    root: Path | str,
    comment_id: str,
    *,
    text: str | None = None,
    resolved: bool | None = None,
) -> dict:
    """Edita el texto y/o marca como resuelto/reabierto. ``KeyError`` si no existe."""
    items = _load(root)
    for item in items:
        if str(item.get("id")) != comment_id:
            continue
        if text is not None:
            cuerpo = text.strip()
            if not cuerpo:
                raise CommentError("el comentario no puede estar vacío")
            if len(cuerpo) > MAX_TEXT_CHARS:
                raise CommentError(f"el comentario supera {MAX_TEXT_CHARS} caracteres")
            item["text"] = cuerpo
        if resolved is not None:
            item["resolved"] = bool(resolved)
            item["resolved_at"] = _now() if resolved else ""
        _save(root, items)
        return _normalize(item)
    raise KeyError(comment_id)


def delete_comment(root: Path | str, comment_id: str) -> bool:
    """Elimina un comentario; ``False`` si no existía."""
    items = _load(root)
    restantes = [item for item in items if str(item.get("id")) != comment_id]
    if len(restantes) == len(items):
        return False
    _save(root, restantes)
    return True
