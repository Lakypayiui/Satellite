"""Language identifiers, extensions and agent prefixes.

This module is the single source of truth shared by the LSP client, the
``code_task`` tool and project-language detection. It has no third-party
dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Language:
    id: str
    name: str
    lsp_language_id: str
    extensions: tuple[str, ...]


LANGUAGES: dict[str, Language] = {
    "python": Language("python", "Python", "python", (".py",)),
    "javascript": Language(
        "javascript", "JavaScript", "javascript", (".js", ".mjs", ".cjs", ".jsx")
    ),
    "typescript": Language(
        "typescript", "TypeScript", "typescript", (".ts", ".tsx", ".mts", ".cts")
    ),
    "java": Language("java", "Java", "java", (".java",)),
    "csharp": Language("csharp", "C#", "csharp", (".cs",)),
    "cpp": Language(
        "cpp",
        "C++",
        "cpp",
        (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
    ),
    "sql": Language("sql", "SQL", "sql", (".sql",)),
    "go": Language("go", "Go", "go", (".go",)),
    "php": Language("php", "PHP", "php", (".php",)),
    "rust": Language("rust", "Rust", "rust", (".rs",)),
}

LANGUAGE_ORDER: tuple[str, ...] = tuple(LANGUAGES)

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    extension: language.id
    for language in LANGUAGES.values()
    for extension in language.extensions
}

# Agent ids are `<language>_coder` / `<language>_auditor`.
_AGENT_PREFIXES: tuple[tuple[str, str], ...] = tuple(
    (f"{language_id}_", language_id) for language_id in LANGUAGES
)


def normalize_language(raw: object) -> str | None:
    """Return a canonical language id from a loose user/model value."""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower().replace(" ", "").replace("-", "_")
    aliases = {
        "js": "javascript",
        "node": "javascript",
        "ts": "typescript",
        "c#": "csharp",
        "cs": "csharp",
        "c_sharp": "csharp",
        "c++": "cpp",
        "cc": "cpp",
        "golang": "go",
    }
    value = aliases.get(value, value)
    return value if value in LANGUAGES else None


def language_for_path(path: object) -> str | None:
    """Language id for a path, or None when the extension is unknown."""
    try:
        suffix = Path(str(path)).suffix.lower()
    except (TypeError, ValueError):
        return None
    return _EXTENSION_TO_LANGUAGE.get(suffix)


def language_for_agent(agent_id: object) -> str | None:
    """Infer the language from a `<language>_coder`/`<language>_auditor` id."""
    if not isinstance(agent_id, str):
        return None
    for prefix, language_id in _AGENT_PREFIXES:
        if agent_id.startswith(prefix):
            return language_id
    return None
