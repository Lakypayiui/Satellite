"""Default context optimizer for Satellite (Python port of the C++).

Port of ``context/optimizer/ContextOptimizer.cpp`` (``DefaultContextOptimizer``):
a deterministic, LLM-free filter that selects the most relevant project files
for a task by keyword relevance, subject to a hard token budget.

It is the FIRST filter of the context pipeline (project context is never
"compressed" by an LLM — it is *selected* here by heuristic). The semantic
layer (``semantic.py``) sits ON TOP of this selection and refines it by
understanding the intention.

Metrics mirror the C++: ``tokens_before/after/saved``, ``compression_ratio``
and ``relevance_score``.

Differences from the C++ version (the Python index has no dependency graph
nor symbol signatures): dependency boosting and dependency selection are
omitted; symbols are plain names.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

_STOPWORDS = {
    "de", "la", "el", "del", "los", "las", "que", "para", "con", "por",
    "una", "un", "and", "the", "for", "with", "from", "into",
    "implement", "add", "create", "make", "fix", "change",
}


@dataclass
class ContextSelection:
    """Files/symbols selected for one task under the token budget."""

    selected_files: list[str] = field(default_factory=list)
    selected_symbols: list[str] = field(default_factory=list)
    selected_constraints: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    relevance_score: float = 0.0


@dataclass
class OptimizationStats:
    """Token/relevance metrics of the last optimization."""

    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 0.0
    relevance_score: float = 0.0
    optimization_time_ms: float = 0.0


def _split_words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char.isalnum():
            current.append(char)
        else:
            if current:
                words.append("".join(current))
                current = []
    if current:
        words.append("".join(current))
    return words


def _extract_keywords(description: str, extra: list[str] | None = None) -> list[str]:
    """Keywords from explicit extras + description words, stopword-filtered."""
    keywords: list[str] = []
    for keyword in extra or []:
        k = keyword.lower()
        if len(k) >= 3 and k not in _STOPWORDS:
            keywords.append(k)
    for word in _split_words(description):
        if len(word) >= 3 and word not in _STOPWORDS:
            keywords.append(word)
    return sorted(set(keywords))


def optimize(
    description: str,
    files: list[dict[str, Any]],
    keywords: list[str] | None = None,
    context_requirements: list[str] | None = None,
    token_budget: int = 4000,
    expand_subgraph: bool = True,
) -> tuple[ContextSelection, OptimizationStats]:
    """Select the most relevant files for ``description`` under a budget.

    Two-step selection (the two lower levels of ``contextorelevante.txt``):

    1. Keyword/token filter (deterministic, like the C++ optimizer).
    2. Subgraph expansion: files selected by keywords pull in their internal
       neighbours (``dependencies`` entries that are project paths) while the
       budget allows — the "relevant subgraph" the task touches.

    Args:
        description: the task (goal or step description) in natural language.
        files: index entries ``[{"path", "size", "symbols": [names],
               "dependencies": ["path", "external:x"]}]``.
        keywords: explicit keywords (optional; derived from description).
        context_requirements: agent requirements (optional; weighted x2).
        token_budget: hard cap on estimated tokens (0 = unlimited).
        expand_subgraph: pull in internal dependencies of selected files.
    """
    start = time.perf_counter()
    selection = ContextSelection()
    stats = OptimizationStats()

    if not files:
        stats.optimization_time_ms = (time.perf_counter() - start) * 1000
        return selection, stats

    all_keywords = _extract_keywords(description, keywords)
    for requirement in context_requirements or []:
        r = requirement.lower()
        if len(r) >= 3 and r not in _STOPWORDS:
            all_keywords.append(r)
            all_keywords.append(r)  # doble peso, como en C++
    all_keywords = sorted(set(all_keywords))

    def contains(haystack: str, needle: str) -> bool:
        return needle in haystack.lower()

    scores: list[float] = []
    total_score = 0.0
    for entry in files:
        path = str(entry.get("path", ""))
        score = 0.0
        for kw in all_keywords:
            if contains(path, kw):
                score += 2.0
        for symbol in entry.get("symbols", []):
            if any(contains(str(symbol), kw) for kw in all_keywords):
                score += 3.0 + 1.0  # símbolo relevante + bonus por archivo
        symbol_keyword_count = sum(
            1
            for symbol in entry.get("symbols", [])
            if any(contains(str(symbol), kw) for kw in all_keywords)
        )
        score += min(symbol_keyword_count, 5)
        scores.append(score)
        total_score += score

    # Orden: score desc, path asc (determinista).
    ordered = sorted(
        range(len(files)),
        key=lambda i: (-scores[i], str(files[i].get("path", ""))),
    )

    selected_paths: list[str] = []
    accumulated_tokens = 0
    selected_score_sum = 0.0
    for idx in ordered:
        entry = files[idx]
        path = str(entry.get("path", ""))
        estimated = max(1, int(entry.get("size", 0)) // 4)
        if token_budget and accumulated_tokens + estimated > token_budget:
            continue
        selected_paths.append(path)
        accumulated_tokens += estimated
        selected_score_sum += scores[idx]

    # Expansión al subgrafo relevante: los seleccionados arrastran sus
    # dependencias internas (deps que son paths del índice) mientras el
    # presupuesto lo permita — el "contexto relevante" del grafo del proyecto.
    if expand_subgraph and token_budget:
        by_path = {str(entry.get("path", "")): entry for entry in files}
        score_by_path = {str(files[i].get("path", "")): scores[i] for i in range(len(files))}
        selected_set = set(selected_paths)
        # Vecinos ordenados por score desc (determinista).
        neighbours: list[str] = []
        for path in list(selected_paths):
            for dep in by_path.get(path, {}).get("dependencies", []) or []:
                if isinstance(dep, str) and dep in by_path and dep not in selected_set:
                    neighbours.append(dep)
                    selected_set.add(dep)
        for dep in sorted(set(neighbours), key=lambda p: (-score_by_path.get(p, 0.0), p)):
            estimated = max(1, int(by_path[dep].get("size", 0)) // 4)
            if accumulated_tokens + estimated > token_budget:
                continue
            selected_paths.append(dep)
            accumulated_tokens += estimated
            selected_score_sum += score_by_path.get(dep, 0.0)

    selection.selected_files = selected_paths
    selection.estimated_tokens = accumulated_tokens
    selection.relevance_score = (
        min(1.0, selected_score_sum / total_score) if total_score > 0.0 else 0.0
    )

    # Símbolos relevantes de archivos seleccionados (fallback: 2 primeros).
    selected_set = set(selected_paths)
    candidate_symbols: list[str] = []
    for entry in files:
        if str(entry.get("path", "")) not in selected_set:
            continue
        for symbol in entry.get("symbols", []):
            if any(contains(str(symbol), kw) for kw in all_keywords):
                candidate_symbols.append(str(symbol))
    if not candidate_symbols:
        for path in selected_paths[:2]:
            for entry in files:
                if str(entry.get("path", "")) == path:
                    candidate_symbols.extend(str(s) for s in entry.get("symbols", []))
                    break
    selection.selected_symbols = candidate_symbols[:20]

    # Restricciones: requirements que sí aparecen en la selección.
    found_constraints: list[str] = []
    for requirement in context_requirements or []:
        rlow = requirement.lower()
        matched = any(contains(p, rlow) for p in selected_paths) or any(
            contains(s, rlow) for s in selection.selected_symbols
        )
        if matched:
            found_constraints.append(requirement)
    selection.selected_constraints = found_constraints or ["sin restricciones detectadas"]

    stats.tokens_before = sum(max(1, int(f.get("size", 0)) // 4) for f in files)
    stats.tokens_after = accumulated_tokens
    stats.tokens_saved = max(0, stats.tokens_before - stats.tokens_after)
    stats.compression_ratio = (
        stats.tokens_saved / stats.tokens_before if stats.tokens_before > 0 else 0.0
    )
    stats.relevance_score = selection.relevance_score
    stats.optimization_time_ms = (time.perf_counter() - start) * 1000
    return selection, stats
