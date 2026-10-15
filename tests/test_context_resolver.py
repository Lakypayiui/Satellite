"""Tests del ContextResolver (resolución just-in-time)."""

import asyncio
from pathlib import Path

from satellite.context.resolver import ContextResolver
from satellite.core.context_models import (
    ContextLimits,
    ContextRef,
    ContextRefKind,
)


def run(coro):
    return asyncio.run(coro)


def make_tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "src" / "other.html").write_text("<p>otro</p>", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Proyecto", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.py").write_text("ignorado", encoding="utf-8")
    return tmp_path


def test_path_reads_only_requested(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    ref = ContextRef(kind=ContextRefKind.PATH, value="src/index.html")
    section = run(resolver.resolve_ref(ref))
    assert section is not None
    assert section.content == "<html></html>"


def test_missing_ref_returns_none(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    section = run(
        resolver.resolve_ref(ContextRef(kind=ContextRefKind.PATH, value="no_existe.txt"))
    )
    assert section is None


def test_pattern_only_matches(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    ref = ContextRef(kind=ContextRefKind.PATTERN, value="src/*.html")
    section = run(resolver.resolve_ref(ref))
    assert section is not None
    assert "index.html" in section.content
    assert "other.html" in section.content
    assert "main.py" not in section.content


def test_pattern_ignores_ignored_dirs(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    ref = ContextRef(kind=ContextRefKind.PATTERN, value="**/*.py")
    section = run(resolver.resolve_ref(ref))
    assert section is not None
    assert "main.py" in section.content
    assert "x.py" not in section.content


def test_dir_lists_one_level(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    ref = ContextRef(kind=ContextRefKind.DIR, value="src")
    section = run(resolver.resolve_ref(ref))
    assert section is not None
    assert "index.html" in section.content
    assert "main.py" in section.content


def test_summary_no_contents(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    ref = ContextRef(kind=ContextRefKind.SUMMARY, value="proyecto")
    section = run(resolver.resolve_ref(ref))
    assert section is not None
    assert "README.md" in section.content
    assert "<html" not in section.content


def test_symbol_search(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    ref = ContextRef(kind=ContextRefKind.SYMBOL, value="foo")
    section = run(resolver.resolve_ref(ref))
    assert section is not None
    assert "def foo" in section.content


def test_limits_trim_bundle(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    refs = (
        ContextRef(kind=ContextRefKind.PATH, value="README.md"),
        ContextRef(kind=ContextRefKind.PATH, value="src/main.py"),
    )
    limits = ContextLimits(max_sections=1)
    bundle = run(resolver.resolve_many(refs, limits))
    assert len(bundle.sections) == 1
    assert bundle.sections[0].ref.value in ("README.md", "src/main.py")


def test_pattern_respects_max_chars_per_section(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    resolver = ContextResolver(root)
    ref = ContextRef(kind=ContextRefKind.PATTERN, value="**/*.html")
    limits = ContextLimits(max_chars_per_section=20)
    section = run(resolver.resolve_ref(ref, limits))
    assert section is not None
    assert len(section.content) <= 20
