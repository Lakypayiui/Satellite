"""Tokenización y utilidades de texto compartidas por contexto y recuperación.

Sin dependencias: una única implementación de tokenización/stopwords para que
``context/optimizer.py`` y el ``Retriever`` no diverjan.
"""

from __future__ import annotations

import math
import re

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿáéíóúüñ0-9_]+")

STOPWORDS: frozenset[str] = frozenset(
    {
        "el", "la", "los", "las", "de", "del", "que", "y", "o", "a", "en", "con",
        "para", "por", "un", "una", "es", "al", "como", "se", "su", "sus", "the",
        "of", "and", "to", "in", "for", "is", "it", "on", "with", "this",
    }
)


def tokenize(text: str) -> list[str]:
    """Divide el texto en tokens (lowercase, palabras/números con ``_``)."""
    return _TOKEN_RE.findall(text.lower())


def keywords(text: str) -> set[str]:
    """Tokens significativos: sin stopwords y de longitud > 2."""
    return {w for w in tokenize(text) if w not in STOPWORDS and len(w) > 2}


def estimate_tokens(text: str) -> int:
    """Estimación de tokens (1 token ≈ 4 caracteres)."""
    return max(1, math.ceil(len(text) / 4))
