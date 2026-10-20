"""Manifiesto del kernel: ids reservados y verificación por hash de los base.

Los specs/prompts de los agentes base son parte del kernel: se cargan del
paquete y, si la copia en datos difiere, se restauran desde el paquete.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

RESERVED_AGENT_IDS: frozenset[str] = frozenset(
    {"orchestrator", "agent_creator", "skill_curator", "pele_diagram_agent"}
)

# Ids del kernel: ni agentes ni skills pueden crearse o sobrescribirse con ellos.
# Cubre **todos** los tools que expone `build_tool_registry()`; hay un test de
# guardia que falla si esta lista se queda atrás.
RESERVED_SKILL_IDS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "run_command",
        "run_argv",
        "job",
        "run_python",
        "git_status",
        "request_context",
        "delegate",
        "spawn_agent",
        "search_skills",
        "propose_skill",
        "render_diagram",
    }
)

RESERVED_IDS: frozenset[str] = RESERVED_AGENT_IDS | RESERVED_SKILL_IDS

DEFAULT_PATTERNS: tuple[str, ...] = ("*.json", "*.txt")


def sha256_bytes(data: bytes) -> str:
    """Hash SHA-256 de un bloque de bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash SHA-256 de un archivo (o cadena vacía si no se puede leer)."""
    try:
        return sha256_bytes(Path(path).read_bytes())
    except OSError:
        return ""


def is_reserved(identifier: str) -> bool:
    """True si el id pertenece al kernel (no creable ni sobrescribible)."""
    return identifier in RESERVED_IDS


def verify_and_restore(
    source_dir: Path,
    data_dir: Path,
    patterns: tuple[str, ...] = DEFAULT_PATTERNS,
) -> list[str]:
    """Verifica la copia en datos contra el paquete y restaura las divergencias.

    Devuelve la lista de nombres de archivo restaurados (vacía si todo coincide).
    El paquete es la fuente de verdad: si falta la copia o difiere, se restaura.
    """
    source_dir = Path(source_dir)
    data_dir = Path(data_dir)
    restored: list[str] = []
    if not source_dir.exists():
        return restored

    for pattern in patterns:
        for src in sorted(source_dir.glob(pattern)):
            dst = data_dir / src.name
            if sha256_file(dst) == sha256_file(src):
                continue
            data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            restored.append(src.name)
    return restored
