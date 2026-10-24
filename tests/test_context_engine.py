"""Tests del DefaultContextEngine."""

import asyncio
from pathlib import Path

from satellite.context.engine import DefaultContextEngine
from satellite.core.context_models import ContextRef, ContextRefKind


def run(coro):
    return asyncio.run(coro)


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("contenido", encoding="utf-8")
    return tmp_path


def test_resolve_basic(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    engine = DefaultContextEngine(root)
    bundle = run(
        engine.resolve([ContextRef(kind=ContextRefKind.PATH, value="a.txt")])
    )
    assert bundle.total_chars == 9
    assert bundle.sections[0].content == "contenido"


def test_expand_resolves_only_new(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    engine = DefaultContextEngine(root)
    (tmp_path / "b.txt").write_text("dos", encoding="utf-8")
    bundle = run(
        engine.expand("n1", [ContextRef(kind=ContextRefKind.PATH, value="b.txt")])
    )
    assert len(bundle.sections) == 1
    assert bundle.sections[0].ref.value == "b.txt"


def test_snapshot_registers(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    engine = DefaultContextEngine(root)
    refs = [ContextRef(kind=ContextRefKind.PATH, value="a.txt")]
    run(engine.resolve(refs))
    snap = run(engine.snapshot("n1"))
    assert snap.node_id == "n1"
    assert snap.total_chars == 9


def test_invalidate_detects_change(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    engine = DefaultContextEngine(root)
    f = tmp_path / "a.txt"
    # primero resolvemos el archivo para que el índice lo conozca
    run(engine.resolve([ContextRef(kind=ContextRefKind.PATH, value="a.txt")]))
    assert engine.resolver.index.needs_refresh(f) is False
    # reescribimos (cambia mtime) e invalidamos
    f.write_text("nuevo contenido", encoding="utf-8")
    run(engine.invalidate(["a.txt"]))
    assert engine.resolver.index.needs_refresh(f) is True


def test_snapshot_default_empty(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    engine = DefaultContextEngine(root)
    snap = run(engine.snapshot("ninguno"))
    assert snap.node_id == "ninguno"
    assert snap.refs == ()


def test_startup_and_reading_one_file_do_not_scan_the_project(tmp_path: Path, monkeypatch) -> None:
    from satellite.context.project_index import ProjectIndex

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("no se debe recorrer el proyecto al construir el runtime")

    monkeypatch.setattr(ProjectIndex, "list_project_files", unexpected_scan)
    engine = DefaultContextEngine(make_root(tmp_path))
    bundle = run(engine.resolve([ContextRef(kind=ContextRefKind.PATH, value="a.txt")]))

    assert bundle.sections[0].content == "contenido"
    assert not engine.resolver.index.index_path.exists()


def test_project_map_still_builds_the_index_on_demand(tmp_path: Path) -> None:
    engine = DefaultContextEngine(make_root(tmp_path))

    assert "a.txt" in engine.resolver.index.project_map()
    assert engine.resolver.index.index_path.exists()
