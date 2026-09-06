"""Tests for the heuristic context optimizer (satellite_py/optimizer.py)."""

from satellite_py import optimizer


def _files():
    return [
        {"path": "src/auth/login.cpp", "size": 8000, "symbols": ["login", "AuthSession"]},
        {"path": "src/payments/billing.cpp", "size": 4000, "symbols": ["charge"]},
        {"path": "README.md", "size": 200, "symbols": []},
    ]


def test_selects_relevant_files_and_metrics():
    files = [
        {"path": "src/auth/login.cpp", "size": 8000, "symbols": ["login", "AuthSession"]},
        {"path": "src/payments/billing.cpp", "size": 4000, "symbols": ["charge"]},
        {"path": "README.md", "size": 200, "symbols": []},
    ]
    # Budget pequeño: entra el relevante (2000 tokens) + README (50);
    # billing.cpp (1000) ya no cabe (2000+1000 > 2500) → queda fuera.
    selection, stats = optimizer.optimize("implementar login de auth", files, token_budget=2500)
    assert selection.selected_files[0] == "src/auth/login.cpp"
    assert "src/payments/billing.cpp" not in selection.selected_files
    assert selection.relevance_score > 0
    assert stats.tokens_before > stats.tokens_after
    assert 0 < stats.compression_ratio <= 1
    assert "login" in selection.selected_symbols


def test_respects_token_budget():
    files = [
        {"path": f"src/file{i}.cpp", "size": 100000, "symbols": [f"sym{i}"]}
        for i in range(10)
    ]
    selection, stats = optimizer.optimize("tarea file5", files, token_budget=100)
    assert selection.estimated_tokens <= 100
    assert stats.tokens_after <= 100


def test_no_files_no_crash():
    selection, stats = optimizer.optimize("algo", [])
    assert selection.selected_files == []
    assert stats.tokens_before == 0


def test_empty_description_still_selects_by_extra_keywords():
    files = [
        {"path": "a/billing.cpp", "size": 800, "symbols": []},
        {"path": "z/other.cpp", "size": 800, "symbols": []},
    ]
    # Presupuesto que solo alcanza para el primer archivo (ordenado por score).
    selection, _ = optimizer.optimize("", files, keywords=["billing"], token_budget=250)
    assert selection.selected_files == ["a/billing.cpp"]


def test_expands_to_relevant_subgraph():
    files = [
        {"path": "src/app.cpp", "size": 800, "symbols": [], "dependencies": ["src/core.h", "external:std"]},
        {"path": "src/core.h", "size": 400, "symbols": ["Core"], "dependencies": []},
        {"path": "src/unrelated.cpp", "size": 800, "symbols": [], "dependencies": []},
    ]
    # Budget 350 tokens: app.cpp (200) + core.h (100) = 300 entran; el vecino
    # externo se ignora y unrelated.cpp (200) ya no cabe (300+200 > 350).
    selection, _ = optimizer.optimize("tarea app", files, token_budget=350)
    assert "src/app.cpp" in selection.selected_files
    # El vecino interno (subgrafo relevante) se incluyó; el externo y el no
    # relacionado no.
    assert "src/core.h" in selection.selected_files
    assert "src/unrelated.cpp" not in selection.selected_files
    assert not any(dep.startswith("external:") for dep in selection.selected_files)


def test_subgraph_expansion_respects_budget():
    files = [
        {"path": "a.cpp", "size": 800, "symbols": [], "dependencies": ["b.h"]},
        {"path": "b.h", "size": 200000, "symbols": [], "dependencies": []},  # 50000 tokens
    ]
    selection, stats = optimizer.optimize("a", files, token_budget=1000)
    assert "a.cpp" in selection.selected_files
    assert "b.h" not in selection.selected_files  # no cabe
    assert stats.tokens_after <= 1000
