"""Cleanup of orphan temporary files left by a crash or a killed process.

Atomic writes remove their own temp on success and on handled failures. A hard
kill can still leave a ``.tmp`` behind, so the runtime sweeps known data roots on
startup and removes only temp files older than a conservative age. Recent files
are left alone: another process may be writing them right now.
"""

from __future__ import annotations

import time
from pathlib import Path

DEFAULT_MAX_AGE_S = 3600.0


def cleanup_orphan_temps(
    roots: tuple[Path | str, ...] | list[Path | str],
    *,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    now: float | None = None,
) -> list[Path]:
    """Delete ``*.tmp`` files under ``roots`` older than ``max_age_s``.

    Returns the removed paths. Missing/unreadable roots and files are ignored:
    cleanup is best-effort and must never prevent the runtime from starting.
    """
    cutoff = (time.time() if now is None else now) - max(0.0, float(max_age_s))
    removed: list[Path] = []
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        try:
            candidates = list(base.rglob("*.tmp"))
        except OSError:
            continue
        for path in candidates:
            try:
                if not path.is_file() or path.stat().st_mtime > cutoff:
                    continue
                path.unlink()
                removed.append(path)
            except OSError:
                continue
    return removed


__all__ = ["DEFAULT_MAX_AGE_S", "cleanup_orphan_temps"]
