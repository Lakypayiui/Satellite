"""File URI and LSP position helpers (tool API is 1-based)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


def path_to_uri(path: Path | str) -> str:
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> Path | None:
    try:
        parsed = urlparse(str(uri))
    except ValueError:
        return None
    if parsed.scheme != "file":
        return None
    raw = unquote(parsed.path or "")
    if os.name == "nt" and raw.startswith("/") and len(raw) >= 3 and raw[2] == ":":
        raw = raw[1:]
    if not raw:
        return None
    return Path(raw)


def to_lsp_position(line: int, column: int) -> dict:
    return {"line": max(0, int(line) - 1), "character": max(0, int(column) - 1)}


def from_lsp_position(position: object) -> tuple[int, int]:
    if not isinstance(position, dict):
        return 1, 1
    try:
        line = int(position.get("line", 0)) + 1
        character = int(position.get("character", 0)) + 1
    except (TypeError, ValueError):
        return 1, 1
    return max(1, line), max(1, character)


def from_lsp_range(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {
            "line": 1,
            "column": 1,
            "end_line": 1,
            "end_column": 1,
        }
    line, column = from_lsp_position(raw.get("start"))
    end_line, end_column = from_lsp_position(raw.get("end"))
    return {
        "line": line,
        "column": column,
        "end_line": end_line,
        "end_column": end_column,
    }
