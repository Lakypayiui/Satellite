"""Resolución determinista de nombres de archivo/carpeta del proyecto.

Cuando alguien pide "el archivo X" a secas, hay que decidir a qué ruta se refiere
sin adivinar. El orden manda: primero la **ruta exacta** que se dio, después el
**nombre exacto** y sólo entonces las coincidencias **difusas** (subcadena). Si el
nivel que gana tiene más de una coincidencia, el resultado es **ambiguo** y se
listan las candidatas: nunca se elige una al azar.

Todo se comprueba sobre la ruta **canónica** (``resolve()``), así que ``..``,
absolutas y symlinks/junctions que apunten fuera del proyecto caen solos, y se
respetan las exclusiones de archivos sensibles (no se ofrecen como candidatos).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from satellite.context.paths import canonical, is_within
from satellite.context.project_index import IGNORED_DIRS
from satellite.context.sensitive import is_index_excluded, is_sensitive_path

TIER_EXACT_PATH = "exact_path"
TIER_EXACT_NAME = "exact_name"
TIER_FUZZY = "fuzzy"

# Archivos gigantes no entran como candidatos (alineado con el índice).
_MAX_ENTRY_BYTES = 1_000_000

__all__ = [
    "Candidate",
    "Lookup",
    "entries_for",
    "lookup",
    "TIER_EXACT_PATH",
    "TIER_EXACT_NAME",
    "TIER_FUZZY",
]


@dataclass(frozen=True)
class Candidate:
    """Una ruta candidata, con el nivel de coincidencia que la encontró."""

    relpath: str
    kind: str  # "file" | "dir"
    match: str  # tier: exact_path | exact_name | fuzzy

    def to_dict(self) -> dict:
        return {"path": self.relpath, "kind": self.kind, "match": self.match}


@dataclass(frozen=True)
class Lookup:
    """Resultado de resolver un valor: ganador único, ambiguo, o nada."""

    value: str
    matches: tuple[Candidate, ...] = ()
    tier: str = ""

    @property
    def ambiguous(self) -> bool:
        """True si el nivel ganador tiene más de una coincidencia."""
        return bool(self.tier) and len(self.matches) > 1

    @property
    def winner(self) -> Candidate | None:
        """La única candidata del nivel ganador, o ``None`` si no hay/ hay varias."""
        return self.matches[0] if len(self.matches) == 1 else None

    @property
    def candidates(self) -> tuple[str, ...]:
        """Rutas de las coincidencias del nivel ganador (para el mensaje de ambigüedad)."""
        return tuple(candidate.relpath for candidate in self.matches)

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "tier": self.tier,
            "ambiguous": self.ambiguous,
            "matches": [candidate.to_dict() for candidate in self.matches],
        }


def entries_for(root: Path | str) -> tuple[tuple[str, str, str], ...]:
    """Entradas del proyecto ``(relpath, kind, name)``, sin sensibles ni escapes.

    Recorre el árbol una sola vez (ignorando los directorios de siempre) y descarta lo
    que no resuelve dentro de la raíz: un symlink que apunte fuera no es una entrada.
    """
    base = canonical(root) or Path(root)
    out: list[tuple[str, str, str]] = []
    for raw_root, dirs, names in os.walk(base):
        directory = Path(raw_root)
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        for name in dirs:
            path = directory / name
            rel = _relpath(path, base)
            if rel is not None and _allowed(path, base):
                out.append((rel, "dir", name))
        for name in names:
            path = directory / name
            rel = _relpath(path, base)
            if rel is not None and _allowed(path, base):
                out.append((rel, "file", name))
    out.sort()
    return tuple(out)


def lookup(
    root: Path | str,
    value: str,
    *,
    entries: tuple[tuple[str, str, str], ...] | None = None,
) -> Lookup:
    """Resuelve ``value`` con prioridad exacta > nombre exacto > difusa.

    ``entries`` permite reutilizar un listado ya calculado (varias menciones del mismo
    mensaje no vuelven a recorrer el árbol).
    """
    clean = (value or "").strip()
    if not clean:
        return Lookup(value or "")
    base = canonical(root) or Path(root)

    exact = _exact_path(clean, base)
    if exact is not None:
        return Lookup(clean, (exact,), TIER_EXACT_PATH)

    # Una ruta relativa que no existe NO se reinterpreta como nombre suelto: sería
    # re-apuntar lo que el usuario escribió a otro sitio.
    if "/" in clean or "\\" in clean:
        return Lookup(clean)

    source = entries if entries is not None else entries_for(base)
    exact_names = [
        Candidate(rel, kind, TIER_EXACT_NAME)
        for rel, kind, name in source
        if name == clean
    ]
    if exact_names:
        return Lookup(clean, _unique_sorted(exact_names), TIER_EXACT_NAME)

    needle = clean.lower()
    fuzzy = [
        Candidate(rel, kind, TIER_FUZZY)
        for rel, kind, name in source
        if needle in name.lower()
    ]
    if fuzzy:
        return Lookup(clean, _unique_sorted(fuzzy), TIER_FUZZY)
    return Lookup(clean)


def _exact_path(value: str, base: Path) -> Candidate | None:
    """Candidata si ``value`` es una ruta exacta dentro del proyecto (y no sensible)."""
    raw = Path(value).expanduser()
    probe = raw if raw.is_absolute() else base / raw
    resolved = canonical(probe)
    if resolved is None or not is_within(resolved, base):
        return None
    if is_sensitive_path(resolved) or is_index_excluded(resolved):
        return None
    if resolved.is_dir():
        kind = "dir"
    elif resolved.is_file():
        kind = "file"
    else:
        return None
    rel = _relpath(resolved, base)
    if rel is None:
        return None
    return Candidate(rel, kind, TIER_EXACT_PATH)


def _unique_sorted(candidates: list[Candidate]) -> tuple[Candidate, ...]:
    return tuple(sorted(set(candidates), key=lambda candidate: (candidate.relpath, candidate.kind)))


def _relpath(path: Path, base: Path) -> str | None:
    """Ruta relativa a ``base`` si sigue dentro (tras canonizar); ``None`` si no."""
    try:
        return path.resolve().relative_to(base).as_posix()
    except (OSError, ValueError):
        return None


def _allowed(path: Path, base: Path) -> bool:
    """True si la entrada es ofrecible: no sensible, dentro del proyecto y de tamaño sano."""
    if is_sensitive_path(path) or is_index_excluded(path):
        return False
    resolved = canonical(path)
    if resolved is None or not is_within(resolved, base):
        return False
    if path.is_file():
        try:
            return path.stat().st_size <= _MAX_ENTRY_BYTES
        except OSError:
            return False
    return True
