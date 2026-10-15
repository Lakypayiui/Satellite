"""Índices de agentes y skills sobre un ``Retriever`` (búsqueda sin LLM)."""

from __future__ import annotations

from typing import Any, Iterable

from satellite.core.spec import AgentSpec
from satellite.retrieval.base import Doc, Hit, Retriever
from satellite.retrieval.bm25 import BM25Retriever

DEFAULT_TOP_K = 3
DEFAULT_MIN_SCORE = 0.1


def agent_doc(spec: AgentSpec) -> Doc:
    """Construye el documento buscable de un ``AgentSpec``."""
    text = " ".join([spec.id, spec.name, spec.description, *spec.capabilities])
    return Doc(
        id=spec.id,
        text=text,
        fields={
            "name": spec.name,
            "description": spec.description,
            "capabilities": list(spec.capabilities),
        },
    )


def skill_doc(manifest: Any) -> Doc:
    """Construye el documento buscable de un manifest de skill.

    Acepta un dict o cualquier objeto con ``to_dict`` (p. ej. ``SkillManifest``).
    """
    if isinstance(manifest, dict):
        data = manifest
    elif hasattr(manifest, "to_dict"):
        data = manifest.to_dict()
    else:
        data = {}
    sid = str(data.get("id", ""))
    description = str(data.get("description", ""))
    tags = [str(t) for t in (data.get("tags") or [])]
    examples = [str(e) for e in (data.get("examples") or [])]
    text = " ".join([sid, description, *tags, *examples])
    return Doc(
        id=sid,
        text=text,
        fields={"description": description, "tags": tags},
    )


class _ObjectIndex:
    """Índice genérico id → objeto respaldado por un ``Retriever``."""

    def __init__(self, retriever: Retriever | None = None) -> None:
        self.retriever: Retriever = retriever or BM25Retriever()
        self._by_id: dict[str, Any] = {}

    def rebuild(self, docs: Iterable[tuple[str, Doc, Any]]) -> None:
        """Reindexa desde pares (id, doc, objeto)."""
        self._by_id.clear()
        materialized: list[Doc] = []
        for object_id, doc, obj in docs:
            self._by_id[object_id] = obj
            materialized.append(doc)
        self.retriever.index(materialized)

    def add(self, object_id: str, doc: Doc, obj: Any) -> None:
        """Añade o reemplaza un objeto."""
        self._by_id[object_id] = obj
        self.retriever.index([doc])

    def remove(self, object_id: str) -> None:
        """Elimina un objeto del índice."""
        self._by_id.pop(object_id, None)
        self.retriever.remove(object_id)

    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        min_score: float | None = DEFAULT_MIN_SCORE,
    ) -> list[Hit]:
        """Busca objetos por consulta (hits con score y metadato)."""
        return self.retriever.search(query, top_k=top_k, min_score=min_score)

    def top_for(
        self,
        query: str,
        k: int = DEFAULT_TOP_K,
        min_score: float | None = DEFAULT_MIN_SCORE,
    ) -> list[Any]:
        """Precarga del top-k de objetos (sin LLM)."""
        return [
            self._by_id[hit.id]
            for hit in self.search(query, top_k=k, min_score=min_score)
            if hit.id in self._by_id
        ]

    def has_match(self, query: str, min_score: float | None = DEFAULT_MIN_SCORE) -> bool:
        """True si hay alguna coincidencia por encima del umbral."""
        return bool(self.search(query, top_k=1, min_score=min_score))

    def size(self) -> int:
        """Número de objetos indexados."""
        return len(self._by_id)


class AgentIndex(_ObjectIndex):
    """Índice de ``AgentSpec`` para búsqueda por capacidad."""

    def rebuild_specs(self, specs: Iterable[AgentSpec]) -> None:
        """Reindexa desde una colección de specs."""
        self.rebuild((spec.id, agent_doc(spec), spec) for spec in specs)

    def add_spec(self, spec: AgentSpec) -> None:
        """Añade o reemplaza un spec."""
        self.add(spec.id, agent_doc(spec), spec)


class SkillIndex(_ObjectIndex):
    """Índice de skills para búsqueda/dedup por descripción, tags y ejemplos."""

    def rebuild_skills(self, manifests: Iterable[Any]) -> None:
        """Reindexa desde manifests (dict u objeto con ``to_dict``)."""
        entries = []
        for manifest in manifests:
            doc = skill_doc(manifest)
            entries.append((doc.id, doc, manifest))
        self.rebuild(entries)

    def add_skill(self, manifest: Any) -> None:
        """Añade o reemplaza un manifest de skill."""
        doc = skill_doc(manifest)
        self.add(doc.id, doc, manifest)
