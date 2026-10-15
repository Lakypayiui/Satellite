"""Serialización canónica del prefijo de una petición LLM (determinismo + hash).

El prefijo estable es, en este orden: **tools → system_prompt → catálogo**.
Todo se serializa de forma canónica (claves ordenadas, tools por nombre) para
que dos construcciones con los mismos datos produzcan **bytes idénticos**.
Los agentes y el orquestador no conocen al proveedor: sólo construyen el
prefijo en este orden.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from satellite.core.tool_models import ToolSchema

CANONICAL_SEPARATORS = (",", ":")
TOKEN_MARGIN = 1.15
TOKEN_OVERHEAD = 8


def canonical_json(obj: object) -> str:
    """JSON canónico: claves ordenadas, sin espacios, ASCII estable."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=CANONICAL_SEPARATORS)


def tools_canonical(schemas: Iterable[ToolSchema] | None) -> list[dict]:
    """Tools normalizadas y **ordenadas por nombre** (orden determinista)."""
    normalised = [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
        for tool in (schemas or ())
    ]
    normalised.sort(key=lambda item: item["name"])
    return normalised


def tools_fingerprint(schemas: Iterable[ToolSchema] | None) -> str:
    """Huella de las tools (para detectar cambios de prefijo)."""
    return _sha256(canonical_json(tools_canonical(schemas)))


def prefix_payload(
    tools: Iterable[ToolSchema] | None,
    system_prompt: str,
    catalog: str | None = None,
) -> str:
    """Bytes canónicos del prefijo estable: tools → system → catálogo.

    Prohibido incluir aquí timestamps, UUIDs o contadores: rompería el cache.
    """
    return "\n".join(
        (
            "TOOLS:" + canonical_json(tools_canonical(tools)),
            "SYSTEM:" + (system_prompt or ""),
            "CATALOG:" + (catalog or ""),
        )
    )


def prefix_hash(
    tools: Iterable[ToolSchema] | None,
    system_prompt: str,
    catalog: str | None = None,
) -> str:
    """Hash (sha256) del prefijo estable."""
    return _sha256(prefix_payload(tools, system_prompt, catalog))


def fingerprint(text: str) -> str:
    """sha256 (hex) de un texto: huella estable de spec/catálogo/tools."""
    return _sha256(text)


def canonical_fingerprint(obj: object) -> str:
    """Huella canónica de un objeto (claves ordenadas, sin espacios)."""
    return _sha256(canonical_json(obj))


def estimate_prefix_tokens(payload: str) -> int:
    """Estimación de tokens del prefijo, con margen (el real lo da el `usage`)."""
    from satellite.retrieval.text import estimate_tokens

    return int(estimate_tokens(payload) * TOKEN_MARGIN) + TOKEN_OVERHEAD


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
