"""Auditoría append-only: registro encadenado que nadie puede borrar ni editar.

La API no expone operaciones de borrado ni de modificación. El hash encadenado
(``prev_hash`` → ``hash``) hace detectables las manipulaciones.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT_FILE = "audit.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    """Log de auditoría en ``<root>/audit.jsonl`` (append-only, encadenado)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        """Ruta del archivo de auditoría."""
        return self.root / AUDIT_FILE

    def record(
        self,
        action: str,
        *,
        actor: str = "",
        target: str = "",
        details: dict | None = None,
    ) -> dict:
        """Añade una entrada (append) y devuelve la entrada escrita."""
        if not isinstance(action, str) or not action.strip():
            raise ValueError("AuditLog.record: 'action' no puede estar vacío")

        entry: dict = {
            "timestamp": _now_iso(),
            "action": action,
            "actor": actor,
            "target": target,
            "details": details or {},
        }
        prev_hash = self.last_hash()
        payload = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        entry["prev_hash"] = prev_hash
        entry["hash"] = hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()

        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read_all(self) -> list[dict]:
        """Lee todas las entradas (omite líneas corruptas)."""
        if not self.path.exists():
            return []
        entries: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def last_hash(self) -> str:
        """Hash de la última entrada (cadena vacía si el log está vacío)."""
        entries = self.read_all()
        if not entries:
            return ""
        return str(entries[-1].get("hash", ""))

    def verify_integrity(self) -> bool:
        """Recomputa la cadena de hashes; True si no fue manipulada."""
        prev = ""
        for entry in self.read_all():
            stored = entry.get("hash", "")
            payload = {
                k: v for k, v in entry.items() if k not in ("prev_hash", "hash")
            }
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            expected = hashlib.sha256((prev + payload_json).encode("utf-8")).hexdigest()
            if entry.get("prev_hash", "") != prev or stored != expected:
                return False
            prev = stored
        return True
