"""Tests for the iterative context preprocessor (satellite_py/context.py)."""

import json

from satellite_py.context import ContextPreprocessor, _extract_first_json


class _FakeClient:
    """Scripted LLM client: returns one response per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=500):
        self.calls.append(user_prompt)
        return self.responses.pop(0)


def _write_project(root, files):
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_index(root, files):
    index_path = root / ".satellite" / "context" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "root": str(root),
                "files": [
                    {
                        "path": relative,
                        "size": len(content.encode("utf-8")),
                        "language": "Python" if relative.endswith(".py") else "C++",
                        "symbols": symbols,
                    }
                    for relative, content, symbols in files
                ],
            }
        ),
        encoding="utf-8",
    )


def test_extract_first_json_handles_prose():
    text = 'Aquí está:\n```json\n{"category": "x", "files_needed": ["a.py"]}\n```\nfin'
    payload = _extract_first_json(text)
    assert payload is not None
    assert payload["category"] == "x"


def test_sufficient_context_returns_refined_prompt(tmp_path):
    _write_project(tmp_path, {"main.py": "print('hi')\n"})
    client = _FakeClient(['{"category": "general", "sufficient": true, "description": "ya basta"}'])
    preprocessor = ContextPreprocessor(client=client, project_root=tmp_path, max_rounds=3)
    result = preprocessor.preprocess("sumar dos numeros")
    assert not result.needs_user_input
    assert "sumar dos numeros" in result.refined_prompt
    assert len(client.calls) == 1


def test_iterative_loop_gathers_files_until_sufficient(tmp_path):
    _write_project(tmp_path, {"math_utils.py": "def add(a, b):\n    return a + b\n"})
    _write_index(
        tmp_path,
        [("math_utils.py", "def add(a, b):\n    return a + b\n", ["add"])],
    )
    client = _FakeClient(
        [
            '{"category": "math", "files_needed": ["math_utils.py"], "description": "necesito la funcion"}',
            '{"category": "math", "sufficient": true, "description": "ahora si"}',
        ]
    )
    preprocessor = ContextPreprocessor(client=client, project_root=tmp_path, max_rounds=3)
    result = preprocessor.preprocess("usar la funcion add")
    assert not result.needs_user_input
    assert len(client.calls) == 2
    # El contexto real del archivo se incrustó en la segunda consulta.
    assert "def add(a, b):" in client.calls[1]


def test_resolves_symbols_via_index(tmp_path):
    _write_project(tmp_path, {"lib.py": "def helper():\n    pass\n"})
    _write_index(tmp_path, [("lib.py", "def helper():\n    pass\n", ["helper"])])
    client = _FakeClient(
        [
            '{"category": "sym", "symbols_needed": ["helper"], "description": "dame el simbolo"}',
            '{"category": "sym", "sufficient": true, "description": "ok"}',
        ]
    )
    preprocessor = ContextPreprocessor(client=client, project_root=tmp_path, max_rounds=3)
    result = preprocessor.preprocess("usar helper")
    assert not result.needs_user_input
    assert "def helper():" in client.calls[1]


def test_unresolvable_after_last_round_asks_user(tmp_path):
    _write_project(tmp_path, {"main.py": "print('hi')\n"})
    _write_index(tmp_path, [("main.py", "print('hi')\n", [])])
    client = _FakeClient(
        ['{"category": "docs", "files_needed": ["docs/guide.md"], "description": "falta la guia"}']
    )
    preprocessor = ContextPreprocessor(client=client, project_root=tmp_path, max_rounds=1)
    result = preprocessor.preprocess("documentar el codigo")
    assert result.needs_user_input
    assert "docs/guide.md" in result.user_prompt


def test_missing_file_reported_when_nothing_resolved(tmp_path):
    _write_project(tmp_path, {"main.py": "print('hi')\n"})
    _write_index(tmp_path, [("main.py", "print('hi')\n", [])])
    client = _FakeClient(
        ['{"category": "x", "files_needed": ["nope.txt"], "description": "no existe"}']
    )
    preprocessor = ContextPreprocessor(client=client, project_root=tmp_path, max_rounds=1)
    result = preprocessor.preprocess("algo")
    assert result.needs_user_input
    assert any("nope.txt" in info for info in result.missing_info)


def test_user_input_is_used_in_next_round(tmp_path):
    client = _FakeClient(
        [
            '{"category": "x", "files_needed": ["missing.txt"], "description": "falta"}',
            '{"category": "x", "sufficient": true, "description": "con la info del usuario basta"}',
        ]
    )
    preprocessor = ContextPreprocessor(client=client, project_root=tmp_path, max_rounds=2)
    result = preprocessor.preprocess("tarea", user_input="la info que falta es esta")
    assert not result.needs_user_input
    assert "la info que falta es esta" in client.calls[1]


def test_model_error_returns_user_prompt(tmp_path):
    class _Broken:
        def complete(self, system_prompt, user_prompt, max_tokens=500):
            raise RuntimeError("connection refused")

    preprocessor = ContextPreprocessor(client=_Broken(), project_root=tmp_path, max_rounds=2)
    result = preprocessor.preprocess("algo")
    assert result.needs_user_input
    assert "connection refused" in result.user_prompt


def _write_index_with_mtime(root, files):
    """files: list of (relative, content, symbols, mtime)."""
    index_path = root / ".satellite" / "context" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "root": str(root),
                "files": [
                    {
                        "path": relative,
                        "size": len(content.encode("utf-8")),
                        "language": "Python" if relative.endswith(".py") else "C++",
                        "symbols": symbols,
                        "mtime": mtime,
                    }
                    for relative, content, symbols, mtime in files
                ],
            }
        ),
        encoding="utf-8",
    )


def test_stale_paths_detects_changed_mtime(tmp_path):
    import time

    target = tmp_path / "a.py"
    target.write_text("v1\n", encoding="utf-8")
    # Índice con un mtime del pasado → el archivo actual es más nuevo → stale.
    _write_index_with_mtime(tmp_path, [("a.py", "v1\n", [], int(time.time()) - 10)])
    preprocessor = ContextPreprocessor(client=_FakeClient([]), project_root=tmp_path)
    assert preprocessor.stale_paths(["a.py"]) == ["a.py"]

    # Refrescar el índice al mtime actual → ya no stale.
    preprocessor.refresh_index_entry("a.py")
    assert preprocessor.stale_paths(["a.py"]) == []


def test_refresh_index_entry_clears_stale(tmp_path):
    import time

    target = tmp_path / "a.py"
    target.write_text("v1\n", encoding="utf-8")
    _write_index_with_mtime(tmp_path, [("a.py", "v1\n", [], int(time.time()) - 10)])
    preprocessor = ContextPreprocessor(client=_FakeClient([]), project_root=tmp_path)

    assert preprocessor.stale_paths(["a.py"]) == ["a.py"]

    preprocessor.refresh_index_entry("a.py")
    assert preprocessor.stale_paths(["a.py"]) == []


def test_stale_paths_reports_missing_file_and_missing_index_entry(tmp_path):
    _write_index_with_mtime(tmp_path, [("a.py", "v1\n", [], 1000)])
    preprocessor = ContextPreprocessor(client=_FakeClient([]), project_root=tmp_path)
    # Archivo que no existe en disco
    assert preprocessor.stale_paths(["nope.py"]) == ["nope.py"]
    # Archivo en disco pero sin entrada en el índice (mtime desconocido)
    (tmp_path / "b.py").write_text("x\n", encoding="utf-8")
    assert preprocessor.stale_paths(["b.py"]) == ["b.py"]
