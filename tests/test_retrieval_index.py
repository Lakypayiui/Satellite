"""Tests de AgentIndex/SkillIndex y del invalidate del índice de proyecto (Fase 2)."""

from pathlib import Path

from satellite.context.engine import DefaultContextEngine
from satellite.core.context_models import ContextRef, ContextRefKind
from satellite.core.spec import AgentSpec
from satellite.retrieval.index import AgentIndex, SkillIndex


def make_spec(spec_id: str, description: str, caps: tuple[str, ...]) -> AgentSpec:
    return AgentSpec(
        id=spec_id,
        name=spec_id.replace("_", " ").title(),
        description=description,
        capabilities=caps,
        prompt_ref=spec_id,
    )


def test_agent_index_top_for() -> None:
    index = AgentIndex()
    index.rebuild_specs(
        [
            make_spec("html_writer", "crear y modificar documentos HTML", ("write_html",)),
            make_spec("test_runner", "ejecutar tests del proyecto", ("run_tests",)),
        ]
    )
    top = index.top_for("crear html")
    assert top and top[0].id == "html_writer"
    assert index.has_match("ejecutar tests") is True


def test_agent_index_no_match_below_threshold() -> None:
    index = AgentIndex()
    index.rebuild_specs([make_spec("html_writer", "documentos HTML", ("write_html",))])
    assert index.has_match("consultar el clima en tokio zzqq") is False
    assert index.top_for("consulta sin coincidencia zzqq") == []


def test_agent_index_add_remove() -> None:
    index = AgentIndex()
    index.add_spec(make_spec("a", "hacer algo", ("do_a",)))
    assert index.size() == 1
    index.remove("a")
    assert index.size() == 0


def test_skill_index_accepts_dict_manifest() -> None:
    index = SkillIndex()
    index.rebuild_skills(
        [
            {
                "id": "count_words",
                "description": "cuenta las palabras de un texto",
                "tags": ["texto", "conteo"],
                "examples": ["contar palabras de un párrafo"],
            }
        ]
    )
    top = index.top_for("contar palabras")
    assert top and top[0]["id"] == "count_words"


def test_engine_invalidate_clears_structure(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def login():\n    pass\n", encoding="utf-8")
    engine = DefaultContextEngine(tmp_path)
    # poblar estructura
    engine.resolver.index.structure(target)
    rel = engine.resolver.index._relpath(target)
    assert rel in engine.resolver.index._structure

    import asyncio

    asyncio.run(engine.invalidate(["app.py"]))
    assert rel not in engine.resolver.index._structure
    assert rel not in engine.resolver.index._stats


def test_agent_index_rebuild_replaces() -> None:
    index = AgentIndex()
    index.rebuild_specs([make_spec("a", "crear html", ("write_html",))])
    index.rebuild_specs([make_spec("b", "ejecutar tests", ("run_tests",))])
    assert index.size() == 1
    assert index.top_for("crear html") == []


def test_context_ref_unused_import_guard() -> None:
    # sanity: el módulo de contexto sigue importable tras mover la tokenización
    assert ContextRef(kind=ContextRefKind.SUMMARY, value="x").value == "x"
