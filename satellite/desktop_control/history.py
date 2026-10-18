"""Historial de observaciones de escritorio (F7).

Sólo la observación **más reciente** conserva el texto completo. Las anteriores se
compactan a **una línea** con puntos fijos y documentados:

    obs <snapshot_id> · app=<app|?> · quality=<good|poor> · elementos=<n> · tokens=<n>

Las líneas no contienen títulos, etiquetas, valores ni texto escrito: sólo
metadatos. El historial se separa por sesión+ventana y vive en una caché nueva
``<proyecto>/.satellite/state/desktop_observations.json`` (escritura atómica,
tolerante a archivos corruptos). El snapshot completo ya viene redactado/saneado
por la capa de observación.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

OBSERVATIONS_FILE = Path(".satellite") / "state" / "desktop_observations.json"
MAX_PREVIOUS_LINES = 10
MAX_KEYS = 50


@dataclass(frozen=True)
class ObservationLine:
    """Resumen de una observación anterior (una sola línea, sin contenido de UI)."""

    snapshot_id: str
    app: str = ""
    quality: str = "good"
    elements: int = 0
    tokens: int = 0

    def to_line(self) -> str:
        return (
            f"obs {self.snapshot_id} · app={self.app or '?'} · "
            f"quality={self.quality} · elementos={self.elements} · tokens={self.tokens}"
        )


class ObservationHistory:
    """Caché del historial: última observación completa + previas en una línea."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._memory: dict[str, dict] = {}

    def record(
        self,
        *,
        session_id: str,
        window_id: str,
        snapshot_id: str,
        app: str,
        quality: str,
        elements: int,
        text: str,
        tokens: int,
    ) -> dict:
        """Registra la observación y devuelve las líneas compactadas anteriores."""
        key = f"{session_id or 'local'}:{window_id}"
        data = self._load()
        entry = data.get(key) if isinstance(data.get(key), dict) else {}
        previous = (
            [str(line) for line in entry.get("previous", [])]
            if isinstance(entry.get("previous"), list)
            else []
        )
        latest = entry.get("latest")
        if isinstance(latest, dict):
            previous.append(
                ObservationLine(
                    snapshot_id=str(latest.get("snapshot_id", "")),
                    app=str(latest.get("app", "")),
                    quality=str(latest.get("quality", "good")),
                    elements=int(latest.get("elements", 0) or 0),
                    tokens=int(latest.get("tokens", 0) or 0),
                ).to_line()
            )
            previous = previous[-MAX_PREVIOUS_LINES:]

        data[key] = {
            "previous": previous,
            "latest": {
                "snapshot_id": str(snapshot_id),
                "app": str(app or ""),
                "quality": str(quality or "good"),
                "elements": int(elements or 0),
                "tokens": int(tokens or 0),
                "text": str(text or ""),
            },
        }
        if len(data) > MAX_KEYS:
            data = dict(list(data.items())[-MAX_KEYS:])
        self._save(data)
        return {
            "key": key,
            "latest_snapshot_id": str(snapshot_id),
            "previous": list(previous),
        }

    def _load(self) -> dict:
        if self._memory:
            return self._memory
        if self.path is None:
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        self._memory = data
        return data

    def _save(self, data: dict) -> None:
        # La memoria es la fuente de verdad dentro del proceso aunque falle el disco.
        self._memory = data
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.path)
        except OSError:
            return
