"""Interfaz de proveedor LLM (formato normalizado).

Los providers NUNCA ejecutan tools: **sólo** traducen formato y, si el
proveedor lo permite, colocan los breakpoints de prompt caching. Los agentes y
el orquestador no conocen estos detalles.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator

from satellite.core.messages import LLMRequest, LLMResponse, StreamChunk
from satellite.providers.caching import CacheStrategy, NoCacheStrategy


class LLMProvider(ABC):
    """Traduce LLMRequest/LLMResponse a un proveedor concreto."""

    @property
    def reports_usage(self) -> bool:
        """True si el proveedor devuelve un ``usage`` con contadores."""
        return True

    @property
    def automatic_cache(self) -> bool:
        """True si el proveedor cachea el prefijo sin marcadores explícitos."""
        return False

    def cache_strategy(self) -> CacheStrategy:
        """Estrategia de caching del proveedor (por defecto: ninguna)."""
        return NoCacheStrategy()

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Completa la petición y devuelve la respuesta normalizada."""
        raise NotImplementedError

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Completa la petición cediendo deltas de texto y, al final, la respuesta.

        Contrato: el último chunk lleva ``response`` con la respuesta completa, igual a
        la que devolvería ``complete``. Por defecto no hay streaming real: se llama a
        ``complete`` y se cede el texto entero de una vez, así cualquier proveedor sigue
        siendo válido aunque no implemente streaming.
        """
        response = await self.complete(request)
        if response.content:
            yield StreamChunk(text=response.content)
        yield StreamChunk(response=response)
