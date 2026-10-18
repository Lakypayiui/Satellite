"""Confinamiento de rutas para la lectura de contexto (deny-by-default).

Una lectura de contexto sólo puede tocar rutas dentro de la raíz del proyecto, o
exactamente dentro de una ruta autorizada **para la petición en curso**. Esa
autorización viaja como argumento de la llamada (nunca como estado global), así
que dos peticiones concurrentes no se pisan.

La decisión se toma siempre sobre la ruta **canónica** (``Path.resolve()``), de
modo que ``..``, rutas absolutas y symlinks/junctions que apunten fuera caen
solos.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

__all__ = ["canonical", "is_within", "confine"]


def canonical(path: Path | str) -> Path | None:
    """Ruta canónica (absoluta, sin ``..`` ni symlinks/junctions); ``None`` si falla."""
    try:
        return Path(path).resolve()
    except OSError:
        return None


def is_within(path: Path, root: Path) -> bool:
    """True si ``path`` es ``root`` o cuelga de él (ambos deben ser canónicos)."""
    return path == root or root in path.parents


def confine(
    candidate: Path | str,
    root: Path,
    allowed_paths: Iterable[Path] = (),
) -> Path | None:
    """Ruta canónica legible, o ``None`` si escapa sin autorización puntual.

    ``allowed_paths`` son las rutas que el usuario aprobó para esta petición:
    se conceden ellas y sus descendientes. No se persisten ni se recuerdan.
    """
    resolved = canonical(candidate)
    if resolved is None:
        return None
    base_root = canonical(root)
    if base_root is not None and is_within(resolved, base_root):
        return resolved
    for allowed in allowed_paths or ():
        base = canonical(allowed)
        if base is not None and is_within(resolved, base):
            return resolved
    return None
