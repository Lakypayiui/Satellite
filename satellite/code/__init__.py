"""Language metadata and bounded code/test execution profiles."""

from satellite.code.languages import (
    LANGUAGES,
    Language,
    language_for_agent,
    language_for_path,
    normalize_language,
)

__all__ = [
    "LANGUAGES",
    "Language",
    "language_for_agent",
    "language_for_path",
    "normalize_language",
]
