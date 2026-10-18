"""Menciones ``@...`` del usuario: de lo que escribe a referencias de contexto.

«@src/api.py ¿qué hace?» o «dónde se define @login» se convierten en referencias que el
resolver ya sabe atender (ruta, directorio, patrón o símbolo). Lo que parece una ruta y no
existe se dice al enviar, en vez de omitirse en silencio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from satellite.context.lookup import entries_for, lookup
from satellite.context.paths import canonical, is_within
from satellite.context.resolver import TEXT_EXTENSIONS
from satellite.context.sensitive import is_sensitive_path
from satellite.core.context_models import ContextRef, ContextRefKind

MENTION_RE = re.compile(r"@([^\s@,;]+)")
MAX_MENTIONS = 8
# Tope de candidatas que se listan en el mensaje de ambigüedad (no crecer sin freno).
MAX_AMBIGUOUS_SHOWN = 10


@dataclass(frozen=True)
class ExternalMention:
    """Una mención ``@`` que apunta a una ruta **existente** fuera del proyecto."""

    value: str  # como lo escribió el usuario (sin la ``@``)
    path: Path  # ruta canónica, ya fuera de la raíz
    kind: ContextRefKind  # PATH (fichero) o DIR (directorio)

    def to_dict(self) -> dict:
        return {"value": self.value, "path": str(self.path), "kind": self.kind.value}


@dataclass(frozen=True)
class AmbiguousMention:
    """Una mención que coincide con varias rutas exactas: no se lee, se avisa."""

    value: str
    candidates: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"value": self.value, "candidates": list(self.candidates)}


class Mentions:
    """Menciones de un texto: resolubles, externas, inexistentes, ambiguas e ignoradas.

    Las externas **no** van a ``refs``: leerlas requiere aprobación explícita, así que
    se separan para que quien envía el mensaje las autorice antes de tocar el disco. Las
    ambiguas tampoco: hay que aclarar a cuál se refiere antes de leer nada. Las ignoradas
    (archivo sensible/excluido) tampoco: nunca se leen como contexto, pero se dicen para
    no ofrecerlas como si se hubieran adjuntado.
    """

    def __init__(
        self,
        refs: tuple[ContextRef, ...],
        missing: tuple[str, ...],
        external: tuple[ExternalMention, ...] = (),
        ambiguous: tuple[AmbiguousMention, ...] = (),
        ignored: tuple[str, ...] = (),
    ) -> None:
        self.refs = refs
        self.missing = missing
        self.external = external
        self.ambiguous = ambiguous
        # Menciones que apuntan a un archivo sensible/excluido: no se adjuntan (nunca se
        # leen como contexto) pero sí se dicen, para no fingir que se adjuntaron.
        self.ignored = ignored

    def __bool__(self) -> bool:
        return bool(self.refs or self.external)

    def summary(self) -> str:
        """Texto corto para el log: qué se adjuntó, qué está fuera, qué no, qué es ambiguo."""
        partes = [f"{ref.value} ({ref.kind.value})" for ref in self.refs]
        partes.extend(
            f"{em.value} (fuera del proyecto, requiere aprobación)" for em in self.external
        )
        for amb in self.ambiguous:
            candidatas = ", ".join(amb.candidates[:MAX_AMBIGUOUS_SHOWN])
            if len(amb.candidates) > MAX_AMBIGUOUS_SHOWN:
                candidatas += ", …"
            partes.append(f"ambiguo: {amb.value} (candidatas: {candidatas})")
        for valor in self.ignored:
            partes.append(f"ignorado (sensible/excluido): {valor}")
        if self.missing:
            partes.append("no encontrado: " + ", ".join(self.missing))
        return "; ".join(partes)


def _mentioned_path(value: str, root: Path) -> Path:
    raw = Path(value).expanduser()
    return raw if raw.is_absolute() else root / raw


def _external_mention(value: str, root: Path) -> ExternalMention | None:
    """``ExternalMention`` si ``value`` es una ruta existente fuera de ``root``."""
    resolved = canonical(_mentioned_path(value, root))
    if resolved is None or is_within(resolved, root):
        return None
    if resolved.is_dir():
        return ExternalMention(value=value, path=resolved, kind=ContextRefKind.DIR)
    return ExternalMention(value=value, path=resolved, kind=ContextRefKind.PATH)


def parse(text: str, project_root: Path | str) -> Mentions:
    """Interpreta las menciones del texto, en orden, sin repetir y con tope."""
    root = canonical(project_root) or Path(project_root)
    refs: list[ContextRef] = []
    external: list[ExternalMention] = []
    missing: list[str] = []
    ambiguous: list[AmbiguousMention] = []
    ignored: list[str] = []
    vistos: set[str] = set()
    # El listado del proyecto se calcula a lo sumo una vez y sólo si alguna mención lo
    # necesita (un nombre de fichero sin ruta exacta); las de ruta/patrón no lo tocan.
    entries: list[tuple[tuple[str, str, str], ...]] = []

    def _entries() -> tuple[tuple[str, str, str], ...]:
        if not entries:
            entries.append(entries_for(root))
        return entries[0]

    for bruto in MENTION_RE.findall(text or ""):
        value = bruto.strip().rstrip(".,:;!?")
        if not value or value in vistos:
            continue
        vistos.add(value)
        if len(refs) + len(external) + len(missing) + len(ambiguous) + len(ignored) >= MAX_MENTIONS:
            break
        ref, candidatas = classify_mention(value, root, _entries)
        if candidatas:
            ambiguous.append(AmbiguousMention(value, tuple(candidatas)))
            continue
        if ref is None:
            missing.append(value)
            continue
        if ref.kind in (ContextRefKind.PATH, ContextRefKind.DIR):
            # Un archivo sensible/excluido no se adjunta jamás (el resolver también lo
            # veta). Se dice aquí en vez de ofrecerlo como si se hubiera leído.
            if _is_ignored(ref.value, root):
                ignored.append(value)
                continue
            externa = _external_mention(value, root)
            if externa is not None:
                external.append(externa)
                continue
        refs.append(ref)
    return Mentions(
        tuple(refs), tuple(missing), tuple(external), tuple(ambiguous), tuple(ignored)
    )


def _is_ignored(value: str, root: Path) -> bool:
    """True si ``value`` apunta a un archivo sensible (el que el resolver nunca lee).

    Se usa la comprobación **precisa** (``is_sensitive_path``), la misma que aplica el
    resolver al leer: así una mención a ``secreto.txt`` no se marca como ignorada sólo
    porque su nombre contenga «secret».
    """
    resolved = canonical(_mentioned_path(value, root))
    if resolved is None:
        return False
    return is_sensitive_path(resolved)


def classify_mention(
    value: str,
    project_root: Path | str,
    entries_provider=None,
) -> tuple[ContextRef | None, tuple[str, ...]]:
    """Clasifica una mención: ``(ref, candidatas)``.

    ``ref`` es ``None`` si no se puede resolver; ``candidatas`` no está vacío cuando el
    nombre coincide con varias rutas **exactas** del proyecto (ambiguo: no se lee nada y
    se pide aclaración). La ruta exacta gana siempre al nombre; el nombre exacto, a lo
    difuso (ver :mod:`satellite.context.lookup`).

    ``entries_provider`` (opcional) devuelve el listado del proyecto bajo demanda, para
    reutilizarlo entre menciones de un mismo mensaje sin recorrer el árbol por cada una.
    """
    if any(ch in value for ch in "*?["):
        return ContextRef(ContextRefKind.PATTERN, value), ()
    candidata = Path(value).expanduser()
    path = candidata if candidata.is_absolute() else Path(project_root) / candidata
    try:
        if path.is_dir():
            return ContextRef(ContextRefKind.DIR, value), ()
        if path.is_file():
            return ContextRef(ContextRefKind.PATH, value), ()
    except OSError:
        return None, ()
    era_fichero = "/" in value or "\\" in value or candidata.suffix.lower() in TEXT_EXTENSIONS
    if era_fichero:
        # Parecía un fichero y la ruta exacta no está: se intenta por nombre del proyecto
        # antes de rendirse (exacto antes que difuso; si hay varias, es ambiguo).
        entries = entries_provider() if entries_provider is not None else None
        result = lookup(project_root, value, entries=entries)
        if result.winner is not None:
            kind = ContextRefKind.DIR if result.winner.kind == "dir" else ContextRefKind.PATH
            return ContextRef(kind, result.winner.relpath), ()
        if result.ambiguous:
            return None, result.candidates
        return None, ()  # ni por nombre: se avisa en vez de inventarlo
    return ContextRef(ContextRefKind.SYMBOL, value), ()


def classify(value: str, project_root: Path | str) -> ContextRef | None:
    """Tipo de referencia para una mención; ``None`` si no existe (no se inventa nada)."""
    ref, _candidatas = classify_mention(value, project_root)
    return ref


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
