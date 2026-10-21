"""Project language detection used as planner context."""

from pathlib import Path

from satellite.context.languages import (
    detect_project_languages,
    format_language_hint,
    project_language_hint,
)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detects_and_ignores_vendor_dirs(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py")
    _write(tmp_path / "src" / "b.py")
    _write(tmp_path / "web" / "a.ts")
    _write(tmp_path / "node_modules" / "pkg" / "index.js")
    _write(tmp_path / ".venv" / "lib" / "x.py")
    _write(tmp_path / "build" / "generated.py")
    _write(tmp_path / "README.md")

    counts = detect_project_languages(tmp_path)
    assert [(item.id, item.files) for item in counts] == [
        ("python", 2),
        ("typescript", 1),
    ]
    assert counts[0].percent == 66.7
    assert counts[1].percent == 33.3


def test_empty_project_has_no_hint(tmp_path: Path) -> None:
    assert detect_project_languages(tmp_path) == []
    assert project_language_hint(tmp_path) == ""


def test_hint_mentions_primary_language(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")
    _write(tmp_path / "b.py")
    _write(tmp_path / "c.py")
    _write(tmp_path / "d.ts")
    hint = project_language_hint(tmp_path)
    assert "Python" in hint
    assert "TypeScript" in hint
    assert format_language_hint(detect_project_languages(tmp_path)).startswith(
        "Python:"
    )
