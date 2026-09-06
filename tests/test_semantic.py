"""Tests for the semantic context compressor (satellite_py/semantic.py)."""

from satellite_py.semantic import ContextCompressor, SemanticContextRefiner, _extract_object


class _FakeClient:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=500):
        self.calls.append(user_prompt)
        return self.text


_COMPRESSED = (
    '{"intention": "implementar auth", "constraints": ["usar tokens"], '
    '"references": {"auth.py": "maneja sesiones"}, "status": "nada hecho"}'
)


def test_compress_returns_neutral_json():
    client = _FakeClient(_COMPRESSED)
    result = ContextCompressor(client).compress(
        "implementar auth", "=== auth.py ===\ncodigo largo irrelevante...\n"
    )
    assert result["intention"] == "implementar auth"
    assert result["constraints"] == ["usar tokens"]
    assert "auth.py" in result["references"]
    assert client.calls[0].startswith("Objetivo de la corrida")


def test_compress_includes_previous_compression():
    client = _FakeClient(_COMPRESSED)
    previous = {"intention": "x", "constraints": [], "references": {}, "status": "paso 1 listo"}
    ContextCompressor(client).compress("goal", "", previous=previous)
    assert "paso 1 listo" in client.calls[0]


def test_maybe_recompress_below_threshold_keeps_document():
    client = _FakeClient(_COMPRESSED)
    compressor = ContextCompressor(client)
    original = {"intention": "g", "constraints": [], "references": {}, "status": ""}
    result = compressor.maybe_recompress("g", original, new_output="corto", threshold=100)
    assert result is original
    assert client.calls == []


def test_maybe_recompress_above_threshold_compresses():
    client = _FakeClient(_COMPRESSED)
    compressor = ContextCompressor(client)
    original = {"intention": "g", "constraints": [], "references": {}, "status": ""}
    result = compressor.maybe_recompress("g", original, new_output="x" * 500, threshold=100)
    assert result is not original
    assert len(client.calls) == 1


def test_compress_fallback_when_no_json():
    class _Prose:
        def complete(self, system_prompt, user_prompt, max_tokens=500):
            return "no tengo json, solo texto"

    result = ContextCompressor(_Prose()).compress("goal", "raw")
    assert result["intention"] == "goal"
    assert result["_uncompressed"] == "raw"


def test_extract_object_handles_prose():
    text = 'bla\n{"a": 1, "b": [2]}\ntrailing'
    assert _extract_object(text) == {"a": 1, "b": [2]}


def test_refiner_keeps_only_intention_relevant_files():
    client = _FakeClient(
        '{"keep_files": ["src/auth/login.cpp"], "keep_symbols": ["login"], '
        '"remove_reason": {"src/payments/billing.cpp": "irrelevante para login"}}'
    )
    result = SemanticContextRefiner(client).refine(
        "implementar login",
        ["src/auth/login.cpp", "src/payments/billing.cpp"],
        ["login", "charge"],
    )
    assert result["keep_files"] == ["src/auth/login.cpp"]
    assert result["keep_symbols"] == ["login"]


def test_refiner_never_adds_unlisted_paths():
    client = _FakeClient(
        '{"keep_files": ["src/otro/archivo.cpp", "src/auth/login.cpp"], "keep_symbols": []}'
    )
    result = SemanticContextRefiner(client).refine(
        "login",
        ["src/auth/login.cpp"],
        ["login"],
    )
    assert result["keep_files"] == ["src/auth/login.cpp"]  # el ajeno se filtra


def test_refiner_fallback_keeps_optimizer_selection_when_no_json():
    class _Prose:
        def complete(self, system_prompt, user_prompt, max_tokens=500):
            return "texto sin json"

    result = SemanticContextRefiner(_Prose()).refine("login", ["a.cpp", "b.cpp"], [])
    assert result["keep_files"] == ["a.cpp", "b.cpp"]
