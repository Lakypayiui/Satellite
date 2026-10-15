"""Cálculo de métricas: ahorro por skill, punto de equilibrio y uso del buscador.

La "línea base" (tokens de la primera resolución sin la skill) es SIEMPRE una
estimación: se marca explícitamente en el informe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

BASELINE_IS_ESTIMATE = True


@dataclass(frozen=True)
class SkillMetrics:
    """Métricas derivadas de una skill."""

    skill_id: str
    uses: int = 0
    successes: int = 0
    failures: int = 0
    creation_tokens: int = 0
    baseline_tokens_estimate: int = 0
    call_cost_tokens: int = 0
    total_latency_ms: int = 0

    @property
    def avg_latency_ms(self) -> float:
        """Latencia media por uso."""
        return round(self.total_latency_ms / self.uses, 2) if self.uses else 0.0

    @property
    def savings_per_use(self) -> int:
        """Ahorro de tokens por uso (nunca negativo)."""
        return max(0, self.baseline_tokens_estimate - self.call_cost_tokens)

    @property
    def savings(self) -> int:
        """Ahorro total = usos × ahorro_por_uso − costo_de_creación."""
        return self.uses * self.savings_per_use - self.creation_tokens

    @property
    def break_even_uses(self) -> float | None:
        """Usos necesarios para amortizar la creación (None si no ahorra)."""
        if self.savings_per_use <= 0:
            return None
        return round(self.creation_tokens / self.savings_per_use, 2)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "uses": self.uses,
            "successes": self.successes,
            "failures": self.failures,
            "creation_tokens": self.creation_tokens,
            "baseline_tokens_estimate": self.baseline_tokens_estimate,
            "call_cost_tokens": self.call_cost_tokens,
            "avg_latency_ms": self.avg_latency_ms,
            "savings": self.savings,
            "savings_per_use": self.savings_per_use,
            "break_even_uses": self.break_even_uses,
        }


@dataclass(frozen=True)
class MetricsReport:
    """Informe agregado del catálogo y del uso del runtime."""

    skills: tuple[SkillMetrics, ...] = ()
    search_hits: int = 0
    search_misses: int = 0
    duplicates_avoided: int = 0
    agent_tokens: dict = field(default_factory=dict)
    delegation_tokens: int = 0
    baseline_is_estimate: bool = BASELINE_IS_ESTIMATE
    cache: dict = field(default_factory=dict)

    @property
    def total_savings(self) -> int:
        """Ahorro total sumando todas las skills."""
        return sum(metric.savings for metric in self.skills)

    @property
    def search_accuracy(self) -> float:
        """Tasa de aciertos del buscador (0 si no hubo búsquedas)."""
        total = self.search_hits + self.search_misses
        return round(self.search_hits / total, 4) if total else 0.0

    def to_dict(self) -> dict:
        return {
            "skills": [metric.to_dict() for metric in self.skills],
            "total_savings": self.total_savings,
            "search_hits": self.search_hits,
            "search_misses": self.search_misses,
            "search_accuracy": self.search_accuracy,
            "duplicates_avoided": self.duplicates_avoided,
            "agent_tokens": dict(self.agent_tokens),
            "delegation_tokens": self.delegation_tokens,
            "baseline_is_estimate": self.baseline_is_estimate,
            "cache": dict(self.cache),
        }


class MetricsCollector:
    """Acumula eventos de uso (buscador, duplicados, tokens) y produce el informe."""

    def __init__(self) -> None:
        self.search_hits = 0
        self.search_misses = 0
        self.duplicates_avoided = 0
        self.delegation_tokens = 0
        self._agent_tokens: dict[str, int] = {}

    def record_search(self, hit: bool) -> None:
        """Registra un acierto o fallo del buscador."""
        if hit:
            self.search_hits += 1
        else:
            self.search_misses += 1

    def record_duplicate(self) -> None:
        """Registra una creación evitada por dedup."""
        self.duplicates_avoided += 1

    def record_agent_tokens(self, agent_id: str, tokens: int) -> None:
        """Acumula tokens consumidos por un agente."""
        self._agent_tokens[agent_id] = self._agent_tokens.get(agent_id, 0) + int(tokens)

    def record_delegation(self, tokens: int) -> None:
        """Acumula tokens consumidos en delegaciones."""
        self.delegation_tokens += int(tokens)


def build_report(
    manifests: Iterable[Any],
    collector: MetricsCollector | None = None,
    *,
    call_cost_tokens: int = 0,
    cache: dict | None = None,
) -> MetricsReport:
    """Construye el informe a partir de manifests (dict u objeto con ``to_dict``).

    ``call_cost_tokens`` es el coste **real** por uso (con cache): input efectivo
    más salida. Sin cache, el ahorro saldría sobreestimado.
    """
    metrics: list[SkillMetrics] = []
    for manifest in manifests:
        data = manifest if isinstance(manifest, dict) else manifest.to_dict()
        stats = data.get("stats") or {}
        metrics.append(
            SkillMetrics(
                skill_id=str(data.get("id", "")),
                uses=int(stats.get("uses", 0)),
                successes=int(stats.get("successes", 0)),
                failures=int(stats.get("failures", 0)),
                creation_tokens=int(stats.get("creation_tokens", 0)),
                baseline_tokens_estimate=int(stats.get("first_resolution_tokens", 0)),
                call_cost_tokens=int(call_cost_tokens),
                total_latency_ms=int(stats.get("total_latency_ms", 0)),
            )
        )
    metrics.sort(key=lambda metric: -metric.savings)
    collector = collector or MetricsCollector()
    return MetricsReport(
        skills=tuple(metrics),
        search_hits=collector.search_hits,
        search_misses=collector.search_misses,
        duplicates_avoided=collector.duplicates_avoided,
        agent_tokens=dict(collector._agent_tokens),
        delegation_tokens=collector.delegation_tokens,
        cache=dict(cache or {}),
    )
