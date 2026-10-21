"""Detect the programming languages present in a project.

The hint is dynamic planner context, not a routing decision: it tells the model
which language-specific agents can handle a code task when the user did not name
a language.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from satellite.code.languages import LANGUAGES, language_for_path

_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".satellite",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "bin",
        "obj",
        "vendor",
        "__pycache__",
        ".next",
        ".nuxt",
        "coverage",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".gradle",
        "pods",
    }
)


@dataclass(frozen=True)
class LanguageCount:
    id: str
    name: str
    files: int
    percent: float


def detect_project_languages(
    root: Path | str, *, max_files: int = 5000
) -> list[LanguageCount]:
    """Count source files per known language, excluding vendor/build dirs."""
    base = Path(root)
    if not base.is_dir():
        return []
    counts: dict[str, int] = {}
    seen = 0
    try:
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [
                name
                for name in dirs
                if name.lower() not in _EXCLUDED_DIRS
            ]
            for name in files:
                language = language_for_path(name)
                if language is None:
                    continue
                counts[language] = counts.get(language, 0) + 1
                seen += 1
                if seen >= max_files:
                    break
            if seen >= max_files:
                break
    except OSError:
        pass
    total = sum(counts.values())
    if total == 0:
        return []
    result = [
        LanguageCount(
            id=language_id,
            name=LANGUAGES[language_id].name,
            files=count,
            percent=round(count * 100.0 / total, 1),
        )
        for language_id, count in counts.items()
    ]
    return sorted(result, key=lambda item: (-item.files, item.id))


def format_language_hint(counts: list[LanguageCount], *, limit: int = 3) -> str:
    """One-line hint for the planner, or an empty string when there is no code."""
    if not counts:
        return ""
    parts = [
        f"{item.name}: {item.files} files ({item.percent:.1f}%)"
        for item in counts[:limit]
    ]
    return "; ".join(parts)


def project_language_hint(root: Path | str, *, max_files: int = 5000) -> str:
    return format_language_hint(
        detect_project_languages(root, max_files=max_files)
    )
