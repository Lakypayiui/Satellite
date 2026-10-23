"""Checkpoint de un grafo en curso, para reanudarlo si el turno se corta (fase 4).

A medida que un nodo termina, el ejecutor lo anota aquí (``.satellite/state/graph``). Si
el mismo grafo se vuelve a lanzar —p. ej. el usuario reenvía la tarea tras cancelar—, los
nodos ya hechos se saltan en vez de repetirse. La clave es la **forma** del grafo (ids,
agentes, dependencias y objetivo de cada nodo), así un grafo distinto no reusa por error.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from satellite.kernel.paths import resolve_paths


def graph_key(graph_dict: dict) -> str:
    """Huella estable de la forma del grafo (no de su estado de ejecución)."""
    shape = [
        {
            "id": n.get("id"),
            "agent_id": n.get("agent_id"),
            "depends_on": sorted(n.get("depends_on") or []),
            "goal": str((n.get("task") or {}).get("goal") or ""),
        }
        for n in graph_dict.get("nodes", [])
    ]
    canon = json.dumps(shape, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


class GraphCheckpoint:
    """Lee y escribe el progreso de un grafo por proyecto. Nunca lanza hacia el turno."""

    def __init__(self, project_root: Path | str) -> None:
        data = resolve_paths(Path(project_root)).project_data
        self.root = Path(data or (Path(project_root) / ".satellite")) / "state" / "graph"

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, key: str) -> dict:
        """Nodos ya completados de un intento anterior: ``{node_id: outcome_dict}``."""
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        nodes = data.get("nodes") if isinstance(data, dict) else None
        return nodes if isinstance(nodes, dict) else {}

    def save_node(self, key: str, node_id: str, outcome: dict) -> None:
        current = self.load(key)
        # Sólo se guardan nodos terminados de verdad (para reanudar sin rehacer).
        if outcome.get("status") in ("ok", "skipped"):
            current[node_id] = outcome
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(
                json.dumps({"nodes": current}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def clear(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except OSError:
            pass


__all__ = ["GraphCheckpoint", "graph_key"]
