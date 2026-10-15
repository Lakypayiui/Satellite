"""Capacidades por modelo: qué parámetros acepta cada familia.

Los modelos de razonamiento de OpenAI (series ``o`` y ``gpt-5``) **rechazan**
``max_tokens`` —exigen ``max_completion_tokens``— y un ``temperature`` distinto
del de defecto: la llamada falla con HTTP 400. La tabla es por prefijo de id y
se puede sobrescribir desde config (``llm.models.<id>``) para no quedar atados a
ella cuando salga una familia nueva.
"""

from __future__ import annotations

from dataclasses import dataclass

TOKEN_PARAM_DEFAULT = "max_tokens"
TOKEN_PARAM_REASONING = "max_completion_tokens"

_REASONING_PREFIXES: tuple[str, ...] = ("o1", "o3", "o4", "gpt-5")


@dataclass(frozen=True)
class ModelCapabilities:
    """Cómo hay que hablarle a este modelo."""

    reasoning: bool = False
    token_param: str = TOKEN_PARAM_DEFAULT
    accepts_temperature: bool = True


def model_capabilities(model: str, overrides: dict | None = None) -> ModelCapabilities:
    """Capacidades del modelo: tabla por prefijo + sobrescritura explícita."""
    model_lower = (model or "").strip().lower()
    reasoning = any(model_lower.startswith(prefix) for prefix in _REASONING_PREFIXES)
    caps = ModelCapabilities(
        reasoning=reasoning,
        token_param=TOKEN_PARAM_REASONING if reasoning else TOKEN_PARAM_DEFAULT,
        accepts_temperature=not reasoning,
    )
    return _apply_overrides(model_lower, caps, overrides)


def _apply_overrides(model_lower: str, caps: ModelCapabilities, overrides: dict | None) -> ModelCapabilities:
    """Aplica ``llm.models.<id>`` si existe (por id exacto o por prefijo)."""
    if not isinstance(overrides, dict):
        return caps
    entry: object = None
    for key, value in overrides.items():
        key_lower = str(key).lower()
        if key_lower == model_lower:
            entry = value
            break
        if entry is None and isinstance(value, dict) and model_lower.startswith(key_lower):
            entry = value
    if not isinstance(entry, dict):
        return caps
    reasoning = bool(entry.get("reasoning", caps.reasoning))
    token_param = str(
        entry.get("token_param")
        or (TOKEN_PARAM_REASONING if reasoning else TOKEN_PARAM_DEFAULT)
    )
    # si no se dice lo contrario, la temperatura va con el razonamiento
    accepts = entry.get("accepts_temperature")
    return ModelCapabilities(
        reasoning=reasoning,
        token_param=token_param,
        accepts_temperature=(not reasoning) if accepts is None else bool(accepts),
    )
