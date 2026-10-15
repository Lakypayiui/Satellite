"""ContextOptimizer: selección de contexto por relevancia bajo presupuesto.

Replica la idea del viejo optimizer de Satellite_OLD pero agnóstico de
lenguaje: dado el objetivo y el contexto del proyecto (sections), elige los
más relevantes para la tarea respetando un presupuesto de tokens, y reporta
stats (tokens_before/after/saved, compression_ratio, relevance_score).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from satellite.core.context_models import ContextBundle, ContextSection
from satellite.retrieval.text import estimate_tokens
from satellite.retrieval.text import keywords as _keywords
from satellite.retrieval.text import tokenize as _tokenize


@dataclass
class OptimizationStats:
    """Métricas de la optimización de contexto."""

    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 0.0
    relevance_score: float = 0.0
    optimization_time_ms: float = 0.0


@dataclass
class ContextSelection:
    """Resultado de la selección de contexto."""

    selected_sections: tuple[ContextSection, ...] = ()
    estimated_tokens: int = 0
    relevance_score: float = 0.0


def score_section(section: ContextSection, keywords: set[str]) -> float:
    """Relevancia [0,1]: fracción de keywords presentes en la sección."""
    if not keywords:
        return 0.0
    content_words = set(_tokenize(section.content))
    matched = keywords & content_words
    return min(1.0, len(matched) / len(keywords))


class DefaultContextOptimizer:
    """Selecciona las secciones más relevantes dentro del presupuesto."""

    def __init__(self, chars_per_token: int = 4) -> None:
        self.chars_per_token = chars_per_token
        self.last_stats = OptimizationStats()

    def optimize(
        self,
        goal: str,
        bundle: ContextBundle,
        max_tokens: int,
    ) -> ContextSelection:
        """Devuelve la selección más relevante con estimated_tokens <= max_tokens."""
        import time

        started = time.monotonic()
        keywords = _keywords(goal)

        sections = list(bundle.sections)
        tokens_before = sum(estimate_tokens(s.content) for s in sections)
        tokens_before = tokens_before or 1

        scored = [(score_section(s, keywords), s) for s in sections]
        scored.sort(key=lambda item: (-item[0], -estimate_tokens(item[1].content)))

        selected: list[ContextSection] = []
        total = 0
        relevance = 0.0
        for score, section in scored:
            section_tokens = estimate_tokens(section.content)
            if total + section_tokens > max_tokens:
                continue
            selected.append(section)
            total += section_tokens
            relevance += score

        if not selected and sections:
            # presupuesto mínimo: tomar la más relevante aunque sobrepase
            best = max(scored, key=lambda item: item[0])
            selected = [best[1]]
            total = estimate_tokens(best[1].content)

        tokens_after = total
        tokens_saved = max(0, tokens_before - tokens_after)
        compression = tokens_saved / tokens_before if tokens_before else 0.0
        relevance_score = relevance / max(1, len(selected))

        self.last_stats = OptimizationStats(
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_saved,
            compression_ratio=round(compression, 4),
            relevance_score=round(relevance_score, 4),
            optimization_time_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return ContextSelection(
            selected_sections=tuple(selected),
            estimated_tokens=tokens_after,
            relevance_score=relevance_score,
        )
