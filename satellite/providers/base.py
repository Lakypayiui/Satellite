"""Interfaz de proveedor LLM (formato normalizado).

Los providers NUNCA ejecutan tools: **sólo** traducen formato y, si el
proveedor lo permite, colocan los breakpoints de prompt caching. Los agentes y
el orquestador no conocen estos detalles.
"""

from abc import ABC, abstractmethod

from satellite.core.messages import LLMRequest, LLMResponse
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
