"""Execution logger for observability (Python port of ExecutionLog.h/.cpp).

Writes one neutral JSON file per agent invocation under
``.satellite/executions/exec_<id>.json`` (the C++ ``ExecutionLogger`` layout):
execution_id, timestamp, provider/model, agent, input/context/output,
duration, status, error, and the token/relevance metrics of the context
selection that fed that step.
"""

from __future__ import annotations

import glob
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Estados del host/dispatcher (strings que usa el runtime Python).
_KNOWN_KEYS = {
    "execution_id",
    "timestamp_ms",
    "provider",
    "model",
    "agent_id",
    "agent_name",
    "agent_version",
    "input",
    "context",
    "output",
    "duration_ms",
    "status",
    "error_message",
    "tokens_before",
    "tokens_after",
    "tokens_saved",
    "compression_ratio",
    "relevance_score",
}


@dataclass
class ExecutionRecord:
    """One agent invocation (mirrors the C++ ExecutionRecord)."""

    execution_id: str = ""
    timestamp_ms: int = 0
    provider: str = ""
    model: str = ""
    agent_id: int = 0
    agent_name: str = ""
    agent_version: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    duration_ms: float = 0.0
    status: str = ""
    error_message: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 0.0
    relevance_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in _KNOWN_KEYS}


class ExecutionLogger:
    """Persist one JSON file per executed agent step (best-effort)."""

    def __init__(self, log_dir: str) -> None:
        self.log_dir = os.fspath(log_dir)
        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except OSError:
            pass

    def log(self, record: ExecutionRecord) -> str:
        """Write ``exec_<id>.json`` and return the execution id used."""
        exec_id = record.execution_id or str(uuid.uuid4())[:8]
        if not record.timestamp_ms:
            record.timestamp_ms = int(time.time() * 1000)
        path = os.path.join(self.log_dir, f"exec_{exec_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return exec_id

    def count(self) -> int:
        return len(glob.glob(os.path.join(self.log_dir, "exec_*.json")))

    def dir(self) -> str:
        return self.log_dir
