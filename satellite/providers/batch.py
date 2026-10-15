"""Interfaz de **Batch API** — documentada, **no implementada** ([AJ-4] del plan).

Para qué sirve: cuando hay muchas llamadas independientes (curator sobre un
catálogo grande, resúmenes masivos), los proveedores ofrecen lotes con precio
reducido. Aquí se fija el **contrato** para no cambiar a los consumidores el día
que se implemente, pero no hay ninguna implementación: los métodos lanzan
``NotImplementedError`` a propósito (nada de fallbacks silenciosos).

Configuración prevista (todavía inerte)::

    {"llm": {"batch": {"enabled": false, "max_size": 100, "poll_seconds": 30}}}

Estado actual del runtime: **siempre desactivado**. Un proveedor normal no es
``batch-capable`` (``supports_batch`` es ``False``), y el metered provider
delega esta capacidad en el proveedor que envuelva.
"""

from __future__ import annotations

from collections.abc import Sequence

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse

BATCH_ENABLED = False


class BatchCapableProvider:
    """Contrato de un proveedor con soporte de lotes (pendiente de implementar)."""

    supports_batch: bool = False

    async def submit_batch(
        self, requests: Sequence[LLMRequest], *, task_id: str = ""
    ) -> str:
        """Envía un lote de peticiones y devuelve su identificador."""
        raise NotImplementedError(
            "Batch API no implementada: interfaz documentada (llm.batch.enabled=false)"
        )

    async def fetch_batch(self, batch_id: str) -> list[LLMResponse]:
        """Recupera los resultados de un lote (mismo orden que las peticiones)."""
        raise NotImplementedError(
            "Batch API no implementada: interfaz documentada (llm.batch.enabled=false)"
        )


def is_batch_capable(provider: object) -> bool:
    """True si el proveedor (o el que envuelve) declara soporte de lotes."""
    seen: set[int] = set()
    current = provider
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if bool(getattr(current, "supports_batch", False)):
            return True
        current = getattr(current, "inner", None)
    return False


def submit_batch(provider: object, requests: Sequence[LLMRequest], *, task_id: str = "") -> str:
    """Atajo que exige un proveedor con lotes; error explícito si no lo soporta."""
    if not is_batch_capable(provider):
        raise ProviderError(
            "el proveedor configurado no soporta lotes (Batch API no implementada)"
        )
    return provider.submit_batch(requests, task_id=task_id)  # type: ignore[attr-defined]


__all__ = ["BATCH_ENABLED", "BatchCapableProvider", "is_batch_capable", "submit_batch"]
