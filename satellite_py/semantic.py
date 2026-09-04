"""Semantic context compression for Satellite.

The same model that interprets the request also *reduces* it: it removes
redundancy from the gathered context (history + project excerpts) while
preserving the intention, the constraints and the references. The result is
neutral JSON owned by Satellite — never model memory — so switching providers
never forces a rebuild.

The compressed document is deliberately small and structured:

    {
      "intention":    "what the run must accomplish",
      "constraints":  ["non-negotiable restrictions"],
      "references":   {"path": "what that file contributes"},
      "status":       "what has already been done (from step results)"
    }
"""

from __future__ import annotations

import json
from typing import Any

from .llm import LLMClient

_SYSTEM_PROMPT = (
    "Eres el compresor semantico de contexto de Satellite. Recibes el objetivo "
    "de una corrida, fragmentos de contexto del proyecto y resultados de "
    "pasos previos. Debes REDUCIR el texto eliminando redundancia y detalle "
    "irrelevante, conservando intencion, restricciones y referencias "
    "(rutas/simbolos). Responde SOLO con JSON: "
    '{"intention": "...", "constraints": ["..."], '
    '"references": {"ruta/archivo": "que aporta"}, "status": "que ya se hizo"}. '
    "Usa frases cortas. Nunca inventes rutas ni restricciones que no esten en "
    "el texto. Nunca escribas codigo ni ejecutes nada; solo resumes."
)


class ContextCompressor:
    """Compress a growing run context into a small neutral JSON document."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def compress(
        self,
        goal: str,
        raw_context: str,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compress ``goal`` + ``raw_context`` (+ an earlier compression)."""
        parts: list[str] = [f"Objetivo de la corrida: {goal}\n"]
        if previous:
            parts.append(
                "Contexto comprimido previo:\n"
                + json.dumps(previous, ensure_ascii=False, indent=1)
                + "\n"
            )
        if raw_context:
            parts.append("Contexto crudo acumulado:\n" + raw_context)
        prompt = "\n".join(parts)
        text = self.client.complete(_SYSTEM_PROMPT, prompt, max_tokens=1200)
        payload = _extract_object(text)
        if not payload:
            # Fallback determinista: no bloquear el flujo si el modelo no da JSON.
            return {
                "intention": goal,
                "constraints": [],
                "references": {},
                "status": (previous or {}).get("status", ""),
                "_uncompressed": raw_context[:4000],
            }
        return payload

    def maybe_recompress(
        self,
        goal: str,
        compressed: dict[str, Any],
        new_output: str,
        threshold: int,
    ) -> dict[str, Any]:
        """Re-compress when ``new_output`` pushes the doc over ``threshold``.

        Returns the same ``compressed`` when under the threshold (cheap path),
        or a fresh compression of compressed + new output when over it.
        """
        if len(new_output) < threshold:
            return compressed
        raw = (
            f"Resultados de pasos previos:\n{new_output}\n"
        )
        return self.compress(goal, raw, previous=compressed)


def _extract_object(text: str) -> dict[str, Any] | None:
    """Return the first balanced JSON object in ``text`` (robust to prose)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif in_string and char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string and char == "{":
            depth += 1
        elif not in_string and char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : index + 1])
                    return payload if isinstance(payload, dict) else None
                except ValueError:
                    return None
    return None


_REFINE_SYSTEM_PROMPT = (
    "Eres el refinador semantico de contexto de Satellite. Recibes la tarea "
    "(su intencion) y una seleccion de archivos/simbolos del proyecto que un "
    "filtro heuristico por keywords preselecciono. Debes decidir, ENTENDIENDO "
    "la intencion, que archivos y simbolos son realmente necesarios. "
    "Responde SOLO con JSON: "
    '{"keep_files": ["ruta/relativa", ...], '
    '"keep_symbols": ["Simbolo", ...], '
    '"remove_reason": {"ruta": "por que se descarta"}}. '
    "Elimina redundancia y lo irrelevante para la intencion, pero NUNCA "
    "descartes algo que la intencion o las restricciones referencien "
    "explicitamente. Si no hay nada que descartar, devuelve todos los "
    "archivos en keep_files. Nunca inventes rutas que no esten en la entrada."
)


class SemanticContextRefiner:
    """Second filter: LLM refines the heuristic optimizer selection.

    The pipeline is two filters in series:

        1. ``optimizer.optimize(...)``  — deterministic keyword/token filter
           (no LLM) that picks the candidate files for a task.
        2. :class:`SemanticContextRefiner` — the same context model that
           interprets the request decides, understanding the intention,
           which of those candidates are really needed.

    The refiner only ever *removes* from the optimizer selection; it never
    adds paths the heuristic did not pick (the optimizer is the gate).
    """

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def refine(
        self,
        task: str,
        optimizer_files: list[str],
        optimizer_symbols: list[str],
        all_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return ``{"keep_files": [...], "keep_symbols": [...], ...}``.

        When the model does not return JSON (or on error) the optimizer
        selection is kept unchanged (never drop context on a parse failure).
        """
        if not optimizer_files:
            return {"keep_files": [], "keep_symbols": [], "remove_reason": {}}
        files_text = "\n".join(f"- {path}" for path in optimizer_files)
        symbols_text = "\n".join(f"- {symbol}" for symbol in optimizer_symbols) or "(ninguno)"
        prompt = (
            f"Tarea (intencion): {task}\n"
            f"Archivos preseleccionados:\n{files_text}\n"
            f"Simbolos preseleccionados:\n{symbols_text}\n"
            "Que archivos y simbolos conservas?"
        )
        try:
            text = self.client.complete(_REFINE_SYSTEM_PROMPT, prompt, max_tokens=600)
        except Exception:  # noqa: BLE001 - fallback determinista
            return {"keep_files": optimizer_files, "keep_symbols": optimizer_symbols, "remove_reason": {}}
        payload = _extract_object(text)
        if not payload:
            return {"keep_files": optimizer_files, "keep_symbols": optimizer_symbols, "remove_reason": {}}
        keep_files = [
            path
            for path in payload.get("keep_files", [])
            if isinstance(path, str) and path in set(optimizer_files)
        ]
        keep_symbols = [
            symbol
            for symbol in payload.get("keep_symbols", [])
            if isinstance(symbol, str) and symbol in set(optimizer_symbols)
        ]
        if not keep_files:
            keep_files = optimizer_files
        return {
            "keep_files": keep_files,
            "keep_symbols": keep_symbols or optimizer_symbols,
            "remove_reason": payload.get("remove_reason", {}),
        }
