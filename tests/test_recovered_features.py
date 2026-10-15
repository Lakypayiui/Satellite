"""Tests de las funcionalidades recuperadas (preprocessor, optimizer, index, observability)."""

import asyncio
from pathlib import Path

from satellite.context.optimizer import DefaultContextOptimizer
from satellite.context.preprocessor import LocalPreprocessor
from satellite.context.project_index import ProjectIndex
from satellite.core.context_models import ContextBundle, ContextRef, ContextRefKind, ContextSection
from satellite.observability.logger import ExecutionLogger, ExecutionRecord
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


# ---------- LocalPreprocessor ----------

def test_preprocessor_refines_and_detects_user_input(tmp_path: Path) -> None:
    """El LLM devuelve needs_user_input → el preprocesador lo propaga."""

    class FakePreprocLLM(FakeLLM):
        async def complete(self, request):
            from satellite.core.messages import LLMResponse

            self.requests.append(request)
            return LLMResponse(content='{"refined_prompt":"objetivo refinado","needs_user_input":true,"user_prompt":"¿qué framework usas?","missing_info":["stack"],"needs":{"files_needed":[],"symbols_needed":[]}}')

    provider = FakePreprocLLM()
    proc = LocalPreprocessor(provider, tmp_path)
    result = run(proc.preprocess("haz la app"))
    assert result["needs_user_input"] is True
    assert result["user_prompt"] == "¿qué framework usas?"
    assert result["refined_prompt"] == "objetivo refinado"
    assert result["missing_info"] == ["stack"]
    # Se llamó al LLM al menos 1 vez (clasificación del goal)
    assert len(provider.requests) >= 1


def test_preprocessor_clear_goal_no_rounds(tmp_path: Path) -> None:
    """Goal claro sin contexto → devuelve refined_prompt = goal."""

    class FakeClear(FakeLLM):
        async def complete(self, request):
            from satellite.core.messages import LLMResponse

            return LLMResponse(content='{"refined_prompt":"goal claro","needs_user_input":false,"user_prompt":"","missing_info":[],"needs":{"files_needed":[],"symbols_needed":[]}}')

    proc = LocalPreprocessor(FakeClear(), tmp_path)
    result = run(proc.preprocess("hola"))
    assert result["needs_user_input"] is False
    assert result["refined_prompt"] == "goal claro"


# ---------- Context Optimizer ----------

def make_bundle() -> ContextBundle:
    return ContextBundle(sections=(
        ContextSection(ref=ContextRef(ContextRefKind.PATH, "a.py"), content="def auth(): pass\nlogin"),
        ContextSection(ref=ContextRef(ContextRefKind.PATH, "b.py"), content="def main(): pass\nconfig"),
        ContextSection(ref=ContextRef(ContextRefKind.PATH, "c.py"), content="import os\nwidget"),
    ))


def test_optimizer_selects_relevant_within_budget() -> None:
    opt = DefaultContextOptimizer()
    bundle = make_bundle()
    sel = opt.optimize("login auth configuración de usuario", bundle, max_tokens=100)
    assert len(sel.selected_sections) <= 3
    assert sel.estimated_tokens <= 100
    assert 0 <= sel.relevance_score <= 1
    stats = opt.last_stats
    assert stats.tokens_before > 0
    assert stats.tokens_saved >= 0
    assert stats.compression_ratio >= 0


def test_optimizer_stats_reported() -> None:
    opt = DefaultContextOptimizer()
    opt.optimize("login auth", make_bundle(), max_tokens=50)
    stats = opt.last_stats
    assert stats.tokens_before > 0
    assert stats.relevance_score >= 0
    assert stats.optimization_time_ms >= 0


# ---------- ProjectIndex estructural ----------

def test_index_detects_functions_classes_imports(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "import os\nfrom flask import Flask\n\ndef login():\n    pass\n\nclass User:\n    pass\n",
        encoding="utf-8",
    )
    index = ProjectIndex(tmp_path)
    structure = index.structure(tmp_path / "app.py")
    names = [s["name"] for s in structure["functions"]]
    assert "login" in names
    class_names = [s["name"] for s in structure["classes"]]
    assert "User" in class_names
    import_names = [s["name"] for s in structure["imports"]]
    assert any("flask" in n or "os" in n for n in import_names)


def test_index_find_symbol(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def calculate():\n    pass\n", encoding="utf-8")
    index = ProjectIndex(tmp_path)
    hits = index.find_symbol("calculate")
    assert len(hits) == 1
    assert hits[0]["kind"] == "functions"


def test_index_listing_does_not_depend_on_path_walk(tmp_path: Path, monkeypatch) -> None:
    """`Path.walk` llegó en 3.12: en 3.11 el listado reventaba con AttributeError.

    Se simula ese Python quitando el método (es exactamente lo que hacía el
    intérprete del CI, por eso fallaba allí y no en local, que corre 3.12).
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ruido.js").write_text("", encoding="utf-8")

    def sin_walk(self, *args, **kwargs):  # lo que tiene Python 3.11: nada
        raise AttributeError("'PosixPath' object has no attribute 'walk'")

    monkeypatch.setattr(Path, "walk", sin_walk, raising=False)

    index = ProjectIndex(tmp_path)
    assert [p.name for p in index.list_project_files()] == ["a.py"]
    assert index.list_project_files(["**/*.py"]) == [tmp_path / "pkg" / "a.py"]
    assert index.find_symbol("f")[0]["path"] == "pkg/a.py"


def test_index_all_imports(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import json\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import os\n", encoding="utf-8")
    index = ProjectIndex(tmp_path)
    imports = index.all_imports()
    assert any("json" in names for names in imports.values())
    assert any("os" in names for names in imports.values())


# ---------- Observability ----------

def test_execution_logger_roundtrip(tmp_path: Path) -> None:
    logger = ExecutionLogger(tmp_path / ".satellite" / "observability")
    rec = ExecutionRecord(
        agent_id="html_writer",
        status="ok",
        input={"goal": "x"},
        output={"path": "a.html"},
        tokens_before=10,
        tokens_after=5,
        task_id="t1",
    )
    path = logger.log(rec)
    assert path is not None
    assert path.exists()
    loaded = logger.load_all()
    assert len(loaded) == 1
    assert loaded[0].agent_id == "html_writer"
    assert loaded[0].tokens_before == 10


def test_execution_logger_tolerant_corrupt_lines(tmp_path: Path) -> None:
    logger = ExecutionLogger(tmp_path / ".satellite" / "observability")
    logger.log(ExecutionRecord(agent_id="a", status="ok"))
    with open(logger.log_dir / "executions.jsonl", "a", encoding="utf-8") as fh:
        fh.write("no es json\n")
    loaded = logger.load_all()
    assert len(loaded) == 1


def test_execution_id_unique(tmp_path: Path) -> None:
    logger = ExecutionLogger(tmp_path / ".satellite" / "observability")
    ids = {logger.make_execution_id() for _ in range(20)}
    assert len(ids) == 20
