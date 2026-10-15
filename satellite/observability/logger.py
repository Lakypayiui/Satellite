"""ExecutionLogger: registro de ejecuciones de agentes en .satellite/observability/."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ExecutionRecord:
    """Registro de una ejecución de un agente (nodo del grafo)."""

    execution_id: str = ""
    timestamp_ms: int = 0
    provider: str = ""
    model: str = ""
    agent_id: str = ""
    agent_version: str = ""
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    duration_ms: float = 0.0
    status: str = "ok"
    error_message: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 0.0
    relevance_score: float = 0.0
    findings_count: int = 0
    task_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ExecutionLogger:
    """Escribe registros JSONL (una línea por ejecución) bajo .satellite/observability/."""

    def __init__(self, log_dir: Path | None = None) -> None:
        import os

        if log_dir is None:
            project_root = Path(os.environ.get("SATELLITE_PROJECT_ROOT", Path.cwd()))
            log_dir = project_root / ".satellite" / "observability"
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def make_execution_id(self) -> str:
        """exec_<timestamp_ms>_<uuid-corto> (único)."""
        import uuid

        return f"exec_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{uuid.uuid4().hex[:6]}"

    def log(self, record: ExecutionRecord) -> Path | None:
        """Añade un registro al log; devuelve la ruta o None si falla."""
        if not record.execution_id:
            record.execution_id = self.make_execution_id()
        path = self.log_dir / "executions.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            return path
        except OSError:
            return None

    def load_all(self) -> list[ExecutionRecord]:
        """Lee todos los registros (omite líneas corruptas)."""
        path = self.log_dir / "executions.jsonl"
        records: list[ExecutionRecord] = []
        if not path.exists():
            return records
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(ExecutionRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return records

    def count(self) -> int:
        return len(self.load_all())
