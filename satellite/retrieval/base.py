"""Interfaz de recuperación: documentos, hits y el ABC ``Retriever``.

Punto de extensión: cualquier implementación (BM25 hoy; embeddings/híbrido en
el futuro) que respete esta interfaz es intercambiable sin tocar a los
consumidores (``AgentIndex``, ``SkillIndex``, agentes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Doc:
    """Documento indexable: ``text`` es lo buscable; ``fields`` es metadato."""

    id: str
    text: str
    fields: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Doc: id no puede estar vacío")


@dataclass(frozen=True)
class Hit:
    """Resultado de una búsqueda con su puntuación y metadato."""

    id: str
    score: float
    fields: dict = field(default_factory=dict)


class Retriever(ABC):
    """Índice de documentos con búsqueda por consulta (sin LLM)."""

    @abstractmethod
    def index(self, docs: Iterable[Doc]) -> None:
        """Indexa (o reindexa) documentos."""
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 3,
        min_score: float | None = None,
    ) -> list[Hit]:
        """Devuelve los mejores documentos para la consulta."""
        raise NotImplementedError

    @abstractmethod
    def remove(self, doc_id: str) -> None:
        """Elimina un documento del índice."""
        raise NotImplementedError

    @abstractmethod
    def size(self) -> int:
        """Número de documentos indexados."""
        raise NotImplementedError
