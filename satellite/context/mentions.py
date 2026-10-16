"""Menciones ``@...`` del usuario: de lo que escribe a referencias de contexto.

«@src/api.py ¿qué hace?» o «dónde se define @login» se convierten en referencias que el
resolver ya sabe atender (ruta, directorio, patrón o símbolo). Lo que parece una ruta y no
existe se dice al enviar, en vez de omitirse en silencio.
"""

from __future__ import annotations

import re
from pathlib import Path

from satellite.context.resolver import TEXT_EXTENSIONS
from satellite.core.context_models import ContextRef, ContextRefKind

MENTION_RE = re.compile(r"@([^\s@,;]+)")
MAX_MENTIONS = 8


class Mentions:
    """Menciones de un texto: las que se pueden resolver y las que no aparecieron."""

    def __init__(self, refs: tuple[ContextRef, ...], missing: tuple[str, ...]) -> None:
        self.refs = refs
        self.missing = missing

    def __bool__(self) -> bool:
        return bool(self.refs)

    def summary(self) -> str:
        """Texto corto para el log: qué se adjuntó y qué no se encontró."""
        partes = [f"{ref.value} ({ref.kind.value})" for ref in self.refs]
        if self.missing:
            partes.append("no encontrado: " + ", ".join(self.missing))
        return "; ".join(partes)


def parse(text: str, project_root: Path | str) -> Mentions:
    """Interpreta las menciones del texto, en orden, sin repetir y con tope."""
    refs: list[ContextRef] = []
    missing: list[str] = []
    vistos: set[str] = set()
    for bruto in MENTION_RE.findall(text or ""):
        value = bruto.strip().rstrip(".,:;!?")
        if not value or value in vistos:
            continue
        vistos.add(value)
        if len(refs) + len(missing) >= MAX_MENTIONS:
            break
        ref = classify(value, project_root)
        if ref is None:
            missing.append(value)
            continue
        refs.append(ref)
    return Mentions(tuple(refs), tuple(missing))


def classify(value: str, project_root: Path | str) -> ContextRef | None:
    """Tipo de referencia para una mención; ``None`` si no existe (no se inventa nada)."""
    if any(ch in value for ch in "*?["):
        return ContextRef(ContextRefKind.PATTERN, value)
    candidata = Path(value).expanduser()
    path = candidata if candidata.is_absolute() else Path(project_root) / candidata
    try:
        if path.is_dir():
            return ContextRef(ContextRefKind.DIR, value)
        if path.is_file():
            return ContextRef(ContextRefKind.PATH, value)
    except OSError:
        return None
    era_fichero = "/" in value or "\\" in value or candidata.suffix.lower() in TEXT_EXTENSIONS
    if era_fichero:
        return None  # parecía un fichero y no está: se avisa en vez de inventarlo
    return ContextRef(ContextRefKind.SYMBOL, value)


def user_refs(task) -> tuple[ContextRef, ...]:
    """Referencias que el usuario adjuntó al mensaje (``Task.metadata['context_refs']``)."""
    from satellite.core.errors import ContractError

    refs: list[ContextRef] = []
    for raw in (getattr(task, "metadata", {}) or {}).get("context_refs") or ():
        try:
            refs.append(raw if isinstance(raw, ContextRef) else ContextRef.from_dict(raw))
        except (ContractError, KeyError, TypeError, ValueError):
            continue
    return tuple(refs)
