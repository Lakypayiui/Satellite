"""Estrategia de prompt caching: qué segmentos del prefijo se marcan.

Vive en el adaptador: agentes y orquestador **no** conocen detalles del
proveedor, sólo construyen el prefijo en orden determinista. La estrategia
decide los breakpoints (a) fin de tools+system, (b) fin del catálogo estable,
(c) último bloque del historial, respetando el máximo del proveedor y sin
marcar prefijos por debajo del mínimo del modelo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from satellite.core.errors import ContractError

TTL_5M = "5m"
TTL_1H = "1h"
VALID_TTLS: tuple[str, ...] = (TTL_5M, TTL_1H)
DEFAULT_TTL = TTL_5M
DEFAULT_MAX_BREAKPOINTS = 4
DEFAULT_MIN_PREFIX_TOKENS = 1024

_TTL_ORDER = {TTL_5M: 1, TTL_1H: 2}
# Prioridad: del final del prefijo hacia atrás (cachear cuanto más, mejor).
_CACHEABLE_SEGMENTS: tuple[str, ...] = ("tail", "catalog", "system")


@dataclass(frozen=True)
class CacheLimits:
    """Límites de caching de un proveedor/modelo concreto."""

    max_breakpoints: int = DEFAULT_MAX_BREAKPOINTS
    min_prefix_tokens: int = DEFAULT_MIN_PREFIX_TOKENS
    ttl: str = DEFAULT_TTL

    def __post_init__(self) -> None:
        if self.ttl not in VALID_TTLS:
            raise ContractError(
                f"CacheLimits: ttl inválido {self.ttl!r} (esperado {VALID_TTLS})"
            )
        if self.max_breakpoints <= 0:
            raise ContractError("CacheLimits: max_breakpoints debe ser > 0")
        if self.min_prefix_tokens < 0:
            raise ContractError("CacheLimits: min_prefix_tokens debe ser >= 0")

    def to_dict(self) -> dict:
        return {
            "max_breakpoints": self.max_breakpoints,
            "min_prefix_tokens": self.min_prefix_tokens,
            "ttl": self.ttl,
        }


@dataclass(frozen=True)
class CachePlan:
    """Qué segmentos se marcan en esta llamada y por qué."""

    ttl: str = DEFAULT_TTL
    mark_system: bool = False
    mark_catalog: bool = False
    mark_tail: bool = False
    skipped_reason: str = ""

    @property
    def breakpoints(self) -> tuple[str, ...]:
        """Segmentos marcados, en el orden del prefijo."""
        return tuple(
            name
            for name, enabled in (
                ("system", self.mark_system),
                ("catalog", self.mark_catalog),
                ("tail", self.mark_tail),
            )
            if enabled
        )

    @property
    def count(self) -> int:
        """Número de breakpoints marcados."""
        return len(self.breakpoints)

    def to_dict(self) -> dict:
        return {
            "ttl": self.ttl,
            "breakpoints": list(self.breakpoints),
            "skipped_reason": self.skipped_reason,
        }


def resolve_ttl(requested: str, previous: str | None) -> tuple[str, bool]:
    """TTL efectivo: nunca se acorta el de un prefijo ya cacheado (1h → 5m).

    Devuelve ``(ttl_efectivo, degradado)``.
    """
    if previous is None:
        return requested, False
    if _TTL_ORDER.get(requested, 0) < _TTL_ORDER.get(previous, 0):
        return previous, True
    return requested, False


class TtlLedger:
    """Recuerda con qué TTL se cacheó cada prefijo (evita mezclas inválidas)."""

    def __init__(self) -> None:
        self._ttls: dict[str, str] = {}

    def effective(self, prefix_hash: str, requested: str) -> tuple[str, bool]:
        """TTL efectivo para ese prefijo + si hubo que degradar la petición."""
        previous = self._ttls.get(prefix_hash)
        ttl, degraded = resolve_ttl(requested, previous)
        self._ttls[prefix_hash] = ttl
        return ttl, degraded

    def forget(self, prefix_hash: str) -> None:
        """Olvida un prefijo (p. ej. al cambiar el modelo o las tools)."""
        self._ttls.pop(prefix_hash, None)


class CacheStrategy(ABC):
    """Decide los breakpoints y construye el marcador del proveedor."""

    provider_name: str = "base"

    @abstractmethod
    def marker(self, ttl: str) -> dict:
        """Objeto `cache_control` nativo del proveedor."""
        raise NotImplementedError

    def supports_breakpoints(self) -> bool:
        """True si el proveedor acepta breakpoints explícitos."""
        return False

    def plan(
        self,
        *,
        prefix_tokens: int,
        has_system: bool,
        has_catalog: bool,
        has_tail: bool,
        limits: CacheLimits,
    ) -> CachePlan:
        """Calcula los breakpoints, o explica por qué no se marca nada."""
        if not self.supports_breakpoints():
            return CachePlan(
                ttl=limits.ttl,
                skipped_reason="el proveedor no acepta breakpoints explícitos",
            )
        if prefix_tokens < limits.min_prefix_tokens:
            return CachePlan(
                ttl=limits.ttl,
                skipped_reason=(
                    f"prefijo ({prefix_tokens} tok) por debajo del mínimo del "
                    f"modelo ({limits.min_prefix_tokens})"
                ),
            )
        available = {
            "system": has_system,
            "catalog": has_catalog,
            "tail": has_tail,
        }
        chosen: list[str] = []
        for segment in _CACHEABLE_SEGMENTS:
            if len(chosen) >= limits.max_breakpoints:
                break
            if available.get(segment):
                chosen.append(segment)
        return CachePlan(
            ttl=limits.ttl,
            mark_system="system" in chosen,
            mark_catalog="catalog" in chosen,
            mark_tail="tail" in chosen,
        )


class NoCacheStrategy(CacheStrategy):
    """Proveedor sin breakpoints explícitos (OpenAI-compat cachea por prefijo)."""

    provider_name = "none"

    def marker(self, ttl: str) -> dict:
        """Sin marcador: este proveedor no acepta `cache_control`."""
        return {}


class AnthropicCacheStrategy(CacheStrategy):
    """Breakpoints explícitos con `cache_control` efímero."""

    provider_name = "anthropic"

    def marker(self, ttl: str) -> dict:
        return {"type": "ephemeral", "ttl": ttl}

    def supports_breakpoints(self) -> bool:
        return True


_STRATEGIES: dict[str, CacheStrategy] = {
    "anthropic": AnthropicCacheStrategy(),
    "none": NoCacheStrategy(),
}

_MODEL_MIN_PREFIX: dict[str, dict[str, int]] = {
    # Mínimos publicados por Anthropic; sobrescribibles por config.
    "anthropic": {"haiku": 2048, "default": 1024},
}


def strategy_for(provider_name: str) -> CacheStrategy:
    """Estrategia del proveedor (por defecto, sin breakpoints)."""
    return _STRATEGIES.get((provider_name or "").lower(), _STRATEGIES["none"])


def limits_for(
    provider_name: str,
    model: str = "",
    *,
    overrides: dict | None = None,
) -> CacheLimits:
    """Límites por proveedor/modelo, con sobrescritura desde config."""
    name = (provider_name or "").lower()
    minima = _MODEL_MIN_PREFIX.get(name, {})
    minimum = minima.get("default", 0)
    model_lower = (model or "").lower()
    for key, value in minima.items():
        if key != "default" and key in model_lower:
            minimum = value
            break
    values: dict = {"min_prefix_tokens": minimum}
    if name != "anthropic":
        values["min_prefix_tokens"] = 0
    for key in ("max_breakpoints", "min_prefix_tokens", "ttl"):
        if isinstance(overrides, dict) and key in overrides:
            values[key] = overrides[key]
    return CacheLimits(**values)


def parse_cache_config(data: dict | None) -> dict:
    """Lee la sección `llm.cache` de config.json (tolerante)."""
    if not isinstance(data, dict):
        return {}
    result: dict = {}
    ttl = str(data.get("ttl", "") or "")
    if ttl in VALID_TTLS:
        result["ttl"] = ttl
    for key, caster in (("max_breakpoints", int), ("min_prefix_tokens", int)):
        if data.get(key) is not None:
            try:
                result[key] = caster(data[key])
            except (TypeError, ValueError):
                continue
    return result
