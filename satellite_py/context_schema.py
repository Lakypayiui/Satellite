"""Neutral context vocabulary owned by Satellite (E6 guarantee).

Everything Satellite persists under ``.satellite/context/`` is **provider
agnostic**: it is plain Satellite JSON with a fixed vocabulary, never model
memory. Changing the LLM provider (local llama.cpp → OpenAI → Anthropic) never
forces a rebuild of the index, the refined prompt or the compressed session
history.

This module is the single source of truth for that vocabulary:

- dataclasses shared by the pipeline stages (``NeedInfo``, ``ContextResult``)
- the neutral session document (``satellite/context-session/1``)
- ``sanitize_session`` — the E6 guard: when persisting, only known neutral
  fields are kept; anything a provider might have slipped in (free text blobs,
  proprietary fields, raw model output) is dropped.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulario neutro de las etapas (NeedInfo / refined prompt)
# ---------------------------------------------------------------------------


@dataclass
class NeedInfo:
    """What the model says it needs from the project (neutral JSON)."""

    category: str = ""
    files_needed: list[str] = field(default_factory=list)
    symbols_needed: list[str] = field(default_factory=list)
    description: str = ""
    sufficient: bool = False


@dataclass
class ContextResult:
    """The final preprocessing decision (neutral JSON)."""

    refined_prompt: str = ""
    needs_user_input: bool = False
    user_prompt: str = ""
    missing_info: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Esquema del documento de sesión (historial comprimido persistido)
# ---------------------------------------------------------------------------

SESSION_SCHEMA = "satellite/context-session/1"

# Campos raíz permitidos del documento de sesión neutro.
_SESSION_ROOT_FIELDS = {
    "schema",
    "session_id",
    "created_ms",
    "goal",
    "provider_agnostic",
    "steps",
    "step_outputs",
    "last_compression",
    "final_context",
    "ok",
    "resumed_from",
}

# Campos permitidos por paso (dentro de session["steps"]).
_SESSION_STEP_FIELDS = {
    "index",
    "agent_id",
    "agent_name",
    "description",
    "status",
    "output",
    "error",
    "context_refreshed",
}

# Campos permitidos del documento comprimido neutro (ContextCompressor /
# SemanticContextRefiner). NO incluye claves de proveedor (model, provider,
# raw...).
_COMPRESSED_FIELDS = {
    "intention",
    "constraints",
    "references",
    "status",
    "remove_reason",
    "keep_files",
    "keep_symbols",
    "_uncompressed",  # fallback determinista acotado (ver semantic.py)
}


def _clean_dict(value: Any, allowed: set[str]) -> Any:
    """Recursively keep only the neutral fields of ``value`` (E6 guard)."""
    if isinstance(value, dict):
        return {k: _clean_dict(v, allowed) for k, v in value.items() if k in allowed}
    if isinstance(value, list):
        return [_clean_dict(v, allowed) for v in value]
    return value


def sanitize_session(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``doc`` containing only the neutral Satellite fields.

    Drops any provider-specific or unknown key at every level (root, steps,
    compressed docs). Agent step outputs are kept as-is: they are JSON
    validated against the agent's output schema, i.e. neutral run data, not
    model memory.
    """
    cleaned = {k: v for k, v in doc.items() if k in _SESSION_ROOT_FIELDS}

    steps = doc.get("steps")
    if isinstance(steps, list):
        cleaned_steps = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_clean = {k: v for k, v in step.items() if k in _SESSION_STEP_FIELDS}
            cleaned_steps.append(step_clean)
        cleaned["steps"] = cleaned_steps

    if "step_outputs" in doc and isinstance(doc["step_outputs"], dict):
        cleaned["step_outputs"] = doc["step_outputs"]
    for key in ("last_compression", "final_context"):
        value = doc.get(key)
        if isinstance(value, dict):
            cleaned[key] = _clean_dict(value, _COMPRESSED_FIELDS)

    return cleaned


def persist_session(path: str, doc: dict[str, Any]) -> None:
    """Sanitize (E6) and write a neutral session document, best-effort."""
    try:
        full = os.fspath(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            json.dump(sanitize_session(doc), handle, ensure_ascii=False, indent=2)
    except OSError:
        pass  # persistencia best-effort: nunca tumba la corrida


def load_session(path: str) -> dict[str, Any]:
    """Load a persisted neutral session document ({} when missing/corrupt)."""
    try:
        with open(os.fspath(path), encoding="utf-8") as handle:
            doc = json.load(handle)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def last_session_path(context_dir: str) -> str:
    """Return the path of the most recent ``session_*.json`` (stable resume)."""
    import glob

    candidates = sorted(glob.glob(os.path.join(os.fspath(context_dir), "session_*.json")))
    return candidates[-1] if candidates else ""
