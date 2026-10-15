"""Listado de modelos de un proveedor (stdlib, sin dependencias nuevas).

Portado de la lógica de modelos de la consola web (ya retirada), con dos mejoras:
- para ``anthropic`` usa ``x-api-key`` + ``anthropic-version`` (con ``Bearer``
  nunca funcionaba contra la API real);
- la URL se normaliza para no duplicar ``/v1``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from satellite.config.catalog import ANTHROPIC, OPENAI

DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_BASE = {
    OPENAI: "https://api.openai.com/v1",
    ANTHROPIC: "https://api.anthropic.com",
}


@dataclass(frozen=True)
class ModelsResult:
    """Resultado del listado: modelos, URL probada y error (si lo hubo)."""

    ok: bool
    models: tuple[str, ...] = ()
    url: str = ""
    error: str = ""
    details: dict = field(default_factory=dict)

    def context_for(self, model: str) -> int | None:
        """Longitud de contexto que **declara el proveedor** para ese modelo."""
        wanted = str(model or "")
        entry = self.details.get(wanted)
        if entry is None and wanted:
            for key, value in self.details.items():
                if key.endswith(wanted) or wanted.endswith(key):
                    entry = value
                    break
        return _context_from_entry(entry) if isinstance(entry, dict) else None


def models_url(base_url: str, family: str = OPENAI) -> str:
    """URL del endpoint de modelos para la familia dada."""
    family = ANTHROPIC if str(family).lower() == ANTHROPIC else OPENAI
    url = str(base_url or "").rstrip("/") or _DEFAULT_BASE[family]
    if url.endswith("/models"):
        return url
    if family == ANTHROPIC:
        return url + "/models" if url.endswith("/v1") else url + "/v1/models"
    return url + "/models"


def _headers(family: str, api_key: str | None) -> dict[str, str]:
    if family == ANTHROPIC:
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    return {"Authorization": f"Bearer {api_key}" if api_key else "Bearer not-needed"}


def _parse(payload: object) -> list[str]:
    return sorted(_parse_details(payload))


def _parse_details(payload: object) -> dict[str, dict]:
    """Modelos con su metadata completa (id → entrada del proveedor)."""
    details: dict[str, dict] = {}
    if isinstance(payload, dict):
        for field in ("data", "models"):
            items = payload.get(field)
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("id") or item.get("name") or "")
                    if model_id:
                        details[model_id] = item
    return details


# Nombres con los que distintos proveedores publican la ventana de contexto.
# OJO: `max_tokens` **no** está aquí a propósito — en Anthropic y en los
# endpoints OpenAI-compatible es el tope de **salida**, no la ventana; tomarlo
# como ventana adelantaría el auto-compactado.
_CONTEXT_KEYS: tuple[str, ...] = (
    "context_length",
    "max_context_length",
    "context_window",
    "max_model_len",
    "max_context_tokens",
    "max_input_tokens",
    "n_ctx",
    "context",
)
_NESTED_KEYS: tuple[str, ...] = ("meta", "metadata", "details", "architecture", "settings")


def _context_from_entry(entry: dict | None) -> int | None:
    """Busca la ventana de contexto en la entrada del modelo (varias formas)."""
    if not isinstance(entry, dict):
        return None
    for source in (entry, *(entry.get(key) for key in _NESTED_KEYS)):
        if not isinstance(source, dict):
            continue
        for key in _CONTEXT_KEYS:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            try:
                tokens = int(value)
            except (TypeError, ValueError):
                continue
            if tokens > 0:
                return tokens
    return None


def fetch_models(
    base_url: str = "",
    family: str = OPENAI,
    api_key: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> ModelsResult:
    """Lista los modelos del proveedor; nunca lanza, siempre informa."""
    family = ANTHROPIC if str(family).lower() == ANTHROPIC else OPENAI
    url = models_url(base_url, family)
    request = urllib.request.Request(url, headers=_headers(family, api_key))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return ModelsResult(ok=False, url=url, error=f"HTTP {exc.code} {exc.reason}")
    except Exception as exc:  # noqa: BLE001 - se reporta tal cual al usuario
        return ModelsResult(ok=False, url=url, error=f"{type(exc).__name__}: {exc}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return ModelsResult(ok=False, url=url, error=f"respuesta no-JSON: {exc}")
    models = _parse(payload)
    if not models:
        return ModelsResult(
            ok=True, url=url, error="la respuesta no traía modelos ('data'/'models' vacío)"
        )
    return ModelsResult(ok=True, models=tuple(models), url=url, details=_parse_details(payload))


def default_model(models: tuple[str, ...] | list[str]) -> str:
    """Modelo sugerido: el primero (ya vienen ordenados) o cadena vacía."""
    return str(models[0]) if models else ""
