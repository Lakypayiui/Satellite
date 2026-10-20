"""Atomic text writes that never leave their temporary file behind.

The pattern is always: create a unique sibling temp file, write+flush (and fsync),
then ``os.replace`` over the target. If anything fails, the temp file is removed
before the exception propagates. On success it disappears because the rename
consumed it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> Path:
    """Write ``content`` to ``path`` atomically and clean the temp file on failure."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            try:
                os.chmod(temp, mode)
            except OSError:
                pass
        os.replace(temp, target)
        return target
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = ["atomic_write_text"]
