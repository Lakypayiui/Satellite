"""Instrucciones del proyecto: ``AGENTS.md`` (o ``SATELLITE.md``) del proyecto.

Son las reglas que el usuario quiere que sigan los agentes en *su* proyecto. Se leen al
vuelo y con tope, para no inundar el contexto de cada nodo.
"""

from __future__ import annotations

from pathlib import Path

from satellite.core.context_models import ContextRef, ContextRefKind

INSTRUCTION_FILES: tuple[str, ...] = ("AGENTS.md", "SATELLITE.md")
MAX_INSTRUCTIONS_CHARS = 8_000


def find_instructions(project_root: Path | str) -> Path | None:
    """El primer fichero de instrucciones que exista (``AGENTS.md`` manda)."""
    base = Path(project_root)
    for name in INSTRUCTION_FILES:
        candidate = base / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def load_instructions(
    project_root: Path | str, *, limit: int = MAX_INSTRUCTIONS_CHARS
) -> tuple[str, str]:
    """``(nombre, texto)``; ``("", "")`` si el proyecto no declara instrucciones."""
    path = find_instructions(project_root)
    if path is None:
        return "", ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "", ""
    if len(content) > limit:
        content = content[:limit] + f"\n\n[truncado: el fichero excede {limit} caracteres]"
    return path.name, content


def instructions_ref(task) -> tuple[ContextRef, ...]:
    """Referencia a las instrucciones del proyecto, si las hay (vacío si no)."""
    path = find_instructions(getattr(task, "project_root", "") or ".")
    if path is None:
        return ()
    return (
        ContextRef(ContextRefKind.PROJECT_RULES, f"instrucciones del proyecto ({path.name})"),
    )
