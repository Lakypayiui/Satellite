"""BM25 determinista sobre un corpus de documentos (sólo stdlib)."""

from __future__ import annotations

import math
from typing import Iterable

from satellite.retrieval.base import Doc, Hit, Retriever
from satellite.retrieval.text import tokenize


class BM25Retriever(Retriever):
    """Índice BM25 (k1/b configurables) sin dependencias externas.

    IDF = ``log((N - df + 0.5) / (df + 0.5) + 1)``; empates resueltos por id
    para que el resultado sea determinista.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 <= 0:
            raise ValueError("BM25Retriever: k1 debe ser > 0")
        if not 0.0 <= b <= 1.0:
            raise ValueError("BM25Retriever: b debe estar en [0, 1]")
        self.k1 = k1
        self.b = b
        self._docs: dict[str, Doc] = {}
        self._tokens: dict[str, list[str]] = {}

    def index(self, docs: Iterable[Doc]) -> None:
        for doc in docs:
            self.remove(doc.id)
            self._docs[doc.id] = doc
            self._tokens[doc.id] = tokenize(doc.text)

    def remove(self, doc_id: str) -> None:
        self._docs.pop(doc_id, None)
        self._tokens.pop(doc_id, None)

    def invalidate(self, doc_id: str) -> None:
        """Alias de ``remove`` (el índice no cachea contenido de disco)."""
        self.remove(doc_id)

    def size(self) -> int:
        return len(self._docs)

    def search(
        self,
        query: str,
        top_k: int = 3,
        min_score: float | None = None,
    ) -> list[Hit]:
        terms = tokenize(query)
        n = len(self._docs)
        if n == 0 or not terms:
            return []
        avgdl = sum(len(t) for t in self._tokens.values()) / n or 1.0
        unique_terms = set(terms)
        df = self._document_frequencies(unique_terms)

        scored: list[Hit] = []
        for doc_id, tokens in self._tokens.items():
            length = len(tokens)
            frequencies: dict[str, int] = {}
            for tok in tokens:
                frequencies[tok] = frequencies.get(tok, 0) + 1
            score = 0.0
            for term in unique_terms:
                tf = frequencies.get(term, 0)
                if tf == 0:
                    continue
                idf = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
                denom = tf + self.k1 * (1.0 - self.b + self.b * length / avgdl)
                score += idf * (tf * (self.k1 + 1.0)) / denom
            if score <= 0.0:
                continue
            if min_score is not None and score < min_score:
                continue
            scored.append(
                Hit(id=doc_id, score=score, fields=dict(self._docs[doc_id].fields))
            )

        scored.sort(key=lambda hit: (-hit.score, hit.id))
        return scored[:top_k]

    def _document_frequencies(self, terms: set[str]) -> dict[str, int]:
        df: dict[str, int] = {}
        for tokens in self._tokens.values():
            for term in terms.intersection(tokens):
                df[term] = df.get(term, 0) + 1
        return df
