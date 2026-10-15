"""Registro por llamada LLM (`calls.jsonl`): hashes y contadores, nunca contenido.

Reglas:
- **Nada de contenido**: sólo ids, hashes, contadores y estado. El prompt, la
  respuesta y las claves no se escriben jamás.
- Append con ``O_APPEND`` y **una escritura por línea** (< 8 KB).
- **Rotación** por tamaño y retención: ``calls.jsonl`` → ``calls.jsonl.1`` … y se
  descartan las más antiguas.
- ``cache_status`` distingue *no reportado* ("unknown") de *reportado y cero*
  ("no cacheado").
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

CALLS_FILE = "calls.jsonl"
MAX_LINE_BYTES = 8192
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_KEEP = 3
MAX_FIELD_CHARS = 200

CACHED = "cached"
WRITTEN = "written"
NOT_CACHED = "no cacheado"
UNKNOWN = "unknown"

KIND_DRIFT = "drift"
KIND_CATALOG_CHANGED = "catalog_changed"
KIND_MODEL_OR_TOOLS_CHANGED = "model_or_tools_changed"
KIND_CHANGED = "changed"


def cache_status(
    cache_creation: int | None, cache_read: int | None
) -> str:
    """Estado de cache de una llamada (``None`` = el proveedor no lo reportó)."""
    if cache_creation is None and cache_read is None:
        return UNKNOWN
    if (cache_read or 0) > 0:
        return CACHED
    if (cache_creation or 0) > 0:
        return WRITTEN
    return NOT_CACHED


@dataclass(frozen=True)
class CallRecord:
    """Una llamada LLM medida (sin contenido)."""

    call_id: str = ""
    ts_ms: int = 0
    task_id: str = ""
    node_id: str = ""
    agent_id: str = ""
    role: str = ""
    model: str = ""
    provider: str = ""
    ttl: str = ""
    prefix_hash: str = ""
    prefix_tokens_estimate: int = 0
    cache_status: str = UNKNOWN
    breakpoints: tuple[int, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation: int | None = None
    cache_read: int | None = None
    spec_fp: str = ""
    catalog_fp: str = ""
    tools_fp: str = ""
    kind: str = ""

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "ts_ms": self.ts_ms,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "model": self.model,
            "provider": self.provider,
            "ttl": self.ttl,
            "prefix_hash": self.prefix_hash,
            "prefix_tokens_estimate": self.prefix_tokens_estimate,
            "cache_status": self.cache_status,
            "breakpoints": list(self.breakpoints),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation": self.cache_creation,
            "cache_read": self.cache_read,
            "spec_fp": self.spec_fp,
            "catalog_fp": self.catalog_fp,
            "tools_fp": self.tools_fp,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CallRecord":
        def _optional(key: str) -> int | None:
            value = d.get(key)
            return None if value is None else int(value)

        return cls(
            call_id=str(d.get("call_id", "")),
            ts_ms=int(d.get("ts_ms", 0)),
            task_id=str(d.get("task_id", "")),
            node_id=str(d.get("node_id", "")),
            agent_id=str(d.get("agent_id", "")),
            role=str(d.get("role", "")),
            model=str(d.get("model", "")),
            provider=str(d.get("provider", "")),
            ttl=str(d.get("ttl", "")),
            prefix_hash=str(d.get("prefix_hash", "")),
            prefix_tokens_estimate=int(d.get("prefix_tokens_estimate", 0)),
            cache_status=str(d.get("cache_status", UNKNOWN)),
            breakpoints=tuple(int(b) for b in (d.get("breakpoints") or ())),
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            cache_creation=_optional("cache_creation"),
            cache_read=_optional("cache_read"),
            spec_fp=str(d.get("spec_fp", "")),
            catalog_fp=str(d.get("catalog_fp", "")),
            tools_fp=str(d.get("tools_fp", "")),
            kind=str(d.get("kind", "")),
        )


class CallRecorder:
    """Escribe ``calls.jsonl`` con append atómico por línea y rotación."""

    def __init__(
        self,
        log_dir: Path | str,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        keep: int = DEFAULT_KEEP,
        filename: str = CALLS_FILE,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.max_bytes = int(max_bytes)
        self.keep = max(1, int(keep))
        self.filename = filename
        self.path = self.log_dir / filename

    def make_call_id(self) -> str:
        """``call_<epoch_ms>_<uuid-corto>``."""
        stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
        return f"call_{stamp}_{uuid.uuid4().hex[:6]}"

    def record(self, record: CallRecord) -> Path | None:
        """Añade una línea; devuelve la ruta o ``None`` si no se pudo escribir."""
        if not record.call_id:
            record = replace(record, call_id=self.make_call_id())
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            line = json.dumps(record.to_dict(), ensure_ascii=False)
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                line = json.dumps(_trim(record).to_dict(), ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return self.path
        except OSError:
            return None

    def load(self, limit: int | None = None) -> list[CallRecord]:
        """Lee las llamadas registradas (omite líneas corruptas)."""
        if not self.path.exists():
            return []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[CallRecord] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(CallRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return records[-limit:] if limit else records

    def count(self) -> int:
        """Número de llamadas registradas."""
        return len(self.load())

    def clear(self) -> None:
        """Borra el registro y sus rotaciones (para tests/reset)."""
        for path in self._rotated_paths():
            if path.exists():
                path.unlink()

    def _rotated_paths(self) -> list[Path]:
        return [self.path] + [
            self.path.with_name(f"{self.filename}.{index}") for index in range(1, self.keep + 1)
        ]

    def _rotate_if_needed(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        oldest = self.path.with_name(f"{self.filename}.{self.keep}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.keep - 1, 0, -1):
            source = self.path.with_name(f"{self.filename}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.filename}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.filename}.1"))


def _trim(record: CallRecord) -> CallRecord:
    """Recorta campos de texto para garantizar líneas < 8 KB."""

    def cut(value: str) -> str:
        return value[:MAX_FIELD_CHARS]

    return replace(
        record,
        task_id=cut(record.task_id),
        node_id=cut(record.node_id),
        agent_id=cut(record.agent_id),
        model=cut(record.model),
        provider=cut(record.provider),
        breakpoints=record.breakpoints[:16],
    )


def default_log_dir(project_root: Path | str) -> Path:
    """``<proyecto>/.satellite/observability``."""
    return Path(project_root) / ".satellite" / "observability"


__all__ = [
    "CACHED",
    "CALLS_FILE",
    "CallRecord",
    "CallRecorder",
    "KIND_CATALOG_CHANGED",
    "KIND_CHANGED",
    "KIND_DRIFT",
    "KIND_MODEL_OR_TOOLS_CHANGED",
    "NOT_CACHED",
    "UNKNOWN",
    "WRITTEN",
    "cache_status",
    "default_log_dir",
]
