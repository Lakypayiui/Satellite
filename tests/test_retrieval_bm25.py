"""Tests del Retriever BM25 (Fase 2)."""

import pytest

from satellite.retrieval.base import Doc
from satellite.retrieval.bm25 import BM25Retriever


def make_retriever() -> BM25Retriever:
    r = BM25Retriever()
    r.index(
        [
            Doc(id="html_writer", text="crear escribir modificar documentos html css"),
            Doc(id="test_runner", text="ejecutar tests pytest reportar resultados"),
            Doc(id="code_analyzer", text="analizar codigo estructura explicar"),
        ]
    )
    return r


def test_search_returns_best_match() -> None:
    hits = make_retriever().search("crear html")
    assert hits
    assert hits[0].id == "html_writer"


def test_search_scores_descending() -> None:
    hits = make_retriever().search("tests pytest")
    assert [h.id for h in hits][0] == "test_runner"
    assert hits[0].score > 0


def test_search_empty_query_returns_empty() -> None:
    assert make_retriever().search("") == []
    assert make_retriever().search("   ") == []


def test_search_no_docs_returns_empty() -> None:
    assert BM25Retriever().search("crear html") == []


def test_remove_drops_doc() -> None:
    r = make_retriever()
    r.remove("html_writer")
    assert r.size() == 2
    assert all(h.id != "html_writer" for h in r.search("crear html"))


def test_min_score_filters() -> None:
    r = make_retriever()
    assert r.search("crear html", min_score=1e9) == []
    assert r.search("crear html", min_score=0.0)


def test_top_k_limits() -> None:
    hits = make_retriever().search("analizar codigo estructura explicar", top_k=1)
    assert len(hits) == 1


def test_deterministic_ties() -> None:
    r = BM25Retriever()
    r.index([Doc(id="b", text="alpha beta"), Doc(id="a", text="alpha beta")])
    first = [h.id for h in r.search("alpha beta")]
    second = [h.id for h in r.search("alpha beta")]
    assert first == second == ["a", "b"]


def test_reindex_replaces_document() -> None:
    r = BM25Retriever()
    r.index([Doc(id="x", text="uno dos")])
    r.index([Doc(id="x", text="tres cuatro")])
    assert r.size() == 1
    assert r.search("uno dos") == []


def test_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        BM25Retriever(k1=0)
    with pytest.raises(ValueError):
        BM25Retriever(b=2.0)


def test_hit_carries_fields() -> None:
    r = BM25Retriever()
    r.index([Doc(id="a", text="crear html", fields={"description": "escribe html"})])
    hit = r.search("crear html")[0]
    assert hit.fields["description"] == "escribe html"
