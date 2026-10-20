"""Cola de instrucciones por proyecto, persistida y ordenada (FIFO).

Vive en ``<proyecto>/.satellite/state/queue.json`` y sobrevive recargas. Es el mecanismo real
de «lo siguiente»: mientras un turno corre, se puede encolar el siguiente prompt (y editarlo
antes de que le toque). No se finge edición *in-flight*: el runtime no ofrece editar el turno
en marcha de forma segura, así que la cola es lo que sí está soportado.

La cancelación es independiente: cancelar corta el turno actual, no vacía la cola.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from satellite.core.atomic import atomic_write_text

QUEUE_FILE = Path(".satellite") / "state" / "queue.json"
MAX_ITEMS = 50
MAX_TEXT_CHARS = 8000


class QueueError(ValueError):
    """Item de cola inválido (el backend lo traduce a un 4xx)."""


def _path(root: Path | str) -> Path:
    return Path(root).resolve() / QUEUE_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(root: Path | str) -> list[dict]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [_normalize(item) for item in data if isinstance(item, dict)]


def _normalize(item: dict) -> dict:
    return {
        "id": str(item.get("id", "")),
        "text": str(item.get("text", "")),
        "plan_mode": bool(item.get("plan_mode", False)),
        "created_at": str(item.get("created_at", "")),
    }


def _save(root: Path | str, items: list[dict]) -> None:
    atomic_write_text(
        _path(root), json.dumps(items, ensure_ascii=False, indent=2)
    )


def list_items(root: Path | str) -> list[dict]:
    """Items encolados en orden (el primero es el siguiente)."""
    return _load(root)


def enqueue(root: Path | str, text: str, *, plan_mode: bool = False) -> list[dict]:
    """Encola un prompt. Devuelve la cola resultante."""
    cuerpo = (text or "").strip()
    if not cuerpo:
        raise QueueError("el prompt encolado no puede estar vacío")
    if len(cuerpo) > MAX_TEXT_CHARS:
        raise QueueError(f"el prompt supera {MAX_TEXT_CHARS} caracteres")
    items = _load(root)
    if len(items) >= MAX_ITEMS:
        raise QueueError(f"la cola está llena ({MAX_ITEMS} items)")
    items.append(
        {"id": uuid4().hex[:12], "text": cuerpo, "plan_mode": bool(plan_mode), "created_at": _now()}
    )
    _save(root, items)
    return items


def update_item(root: Path | str, item_id: str, *, text: str, plan_mode: bool | None = None) -> list[dict]:
    """Edita el texto (y opcionalmente el modo plan) de un item aún no ejecutado."""
    cuerpo = (text or "").strip()
    if not cuerpo:
        raise QueueError("el prompt no puede estar vacío")
    items = _load(root)
    for item in items:
        if item["id"] == item_id:
            item["text"] = cuerpo
            if plan_mode is not None:
                item["plan_mode"] = bool(plan_mode)
            _save(root, items)
            return items
    raise KeyError(item_id)


def remove_item(root: Path | str, item_id: str) -> list[dict]:
    """Saca un item de la cola (no ejecuta nada)."""
    items = _load(root)
    restantes = [item for item in items if item["id"] != item_id]
    if len(restantes) == len(items):
        raise KeyError(item_id)
    _save(root, restantes)
    return restantes


def pop_next(root: Path | str) -> dict | None:
    """Saca y devuelve el primer item (o ``None`` si la cola está vacía).

    Es el paso que usan los drenados explícitos (CLI/TUI): se consume **un** item por
    turno, de modo que cancelar no borra lo que queda.
    """
    items = _load(root)
    if not items:
        return None
    primero, resto = items[0], items[1:]
    _save(root, resto)
    return primero


def clear(root: Path | str) -> list[dict]:
    """Vacía la cola por completo."""
    _save(root, [])
    return []
