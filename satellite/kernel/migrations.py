"""Migraciones de esquema y snapshots/rollback del directorio de datos."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 4
VERSION_FILE = ".schema_version"
SNAPSHOT_DIR = "snapshots"

REQUIRED_SUBDIRS: tuple[str, ...] = (
    "tasks",
    "plans",
    "executions",
    "state",
    "context",
    "logs",
    "config",
    "metrics",
    "snapshots",
)


def current_version(root: Path) -> int:
    """Versión de esquema del directorio de datos (0 si no existe)."""
    path = Path(root) / VERSION_FILE
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _write_version(root: Path, version: int) -> None:
    (Path(root) / VERSION_FILE).write_text(str(version), encoding="utf-8")


def migrate(root: Path, *, target: int = SCHEMA_VERSION) -> list[int]:
    """Aplica las migraciones pendientes; devuelve las versiones aplicadas."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    applied: list[int] = []
    version = current_version(root)
    while version < target:
        version += 1
        _apply(root, version)
        _write_version(root, version)
        applied.append(version)
    return applied


def _apply(root: Path, version: int) -> None:
    """Migración de una versión concreta (hoy: garantiza los subdirectorios)."""
    for sub in REQUIRED_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)


LEGACY_SPLIT_MARKER = "# ENTRADAS"
SYSTEM_SUFFIX = ".system.txt"
USER_SUFFIX = ".user.txt"


def split_legacy_prompt(text: str) -> tuple[str | None, str]:
    """Divide un prompt antiguo en ``(system, user)``.

    Regla determinista: corta en la primera línea ``# ENTRADAS``. Si no existe,
    devuelve ``(None, "")`` → el fichero se cuarentena en vez de adivinar.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip().upper() == LEGACY_SPLIT_MARKER:
            return "".join(lines[:index]).rstrip() + "\n", "".join(lines[index:])
    return None, ""


def migrate_agent_specs(agents_root: Path) -> list[str]:
    """Añade ``lifecycle`` a los specs guardados que no lo traen (esquema v4).

    Idempotente y tolerante: los ficheros ilegibles se dejan como están (el
    cargador del catálogo ya sabe cuarentenarlos), y no se toca ningún otro campo.
    Devuelve los ids migrados.
    """
    root = Path(agents_root)
    migrated: list[str] = []
    if not root.is_dir():
        return migrated
    for file in sorted(root.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "id" not in data or "lifecycle" in data:
            continue
        data["lifecycle"] = "persistent"
        try:
            file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            continue
        migrated.append(str(data.get("id", file.stem)))
    return migrated


def migrate_prompt_files(root: Path) -> list[str]:
    """Convierte los ``<id>.txt`` persistidos al par system/user.

    Carga tolerante: si ya existe el par, el fichero suelto se elimina (está
    superado); si no se puede dividir con confianza, se mueve a cuarentena.
    """
    root = Path(root)
    converted: list[str] = []
    if not root.exists():
        return converted
    for file in sorted(root.glob("*.txt")):
        if file.name.endswith(SYSTEM_SUFFIX) or file.name.endswith(USER_SUFFIX):
            continue
        if (root / f"{file.stem}{SYSTEM_SUFFIX}").exists():
            file.unlink()  # superado por el par
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        system, user = split_legacy_prompt(text)
        if system is None:
            _quarantine_file(root, file)
            continue
        (root / f"{file.stem}{SYSTEM_SUFFIX}").write_text(system, encoding="utf-8")
        (root / f"{file.stem}{USER_SUFFIX}").write_text(user, encoding="utf-8")
        file.unlink()
        converted.append(file.stem)
    return converted


def _quarantine_file(root: Path, file: Path) -> None:
    """Mueve un fichero no migrable a ``_quarantine/``."""
    target_dir = root / "_quarantine"
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(file), str(target_dir / file.name))
    except OSError:
        return


def create_snapshot(root: Path, *, label: str = "") -> Path:
    """Copia el directorio de datos (sin ``snapshots/``) a un snapshot nuevo."""
    root = Path(root)
    snapshots = root / SNAPSHOT_DIR
    snapshots.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    name = f"{stamp}_{label}" if label else stamp
    target = snapshots / name
    shutil.copytree(root, target, ignore=shutil.ignore_patterns(SNAPSHOT_DIR))
    return target


def list_snapshots(root: Path) -> list[Path]:
    """Snapshots existentes, ordenados por nombre (cronológico)."""
    snapshots = Path(root) / SNAPSHOT_DIR
    if not snapshots.exists():
        return []
    return sorted(
        [path for path in snapshots.iterdir() if path.is_dir()],
        key=lambda path: path.name,
    )


def restore_snapshot(root: Path, name: str) -> None:
    """Restaura el contenido del snapshot sobre el directorio de datos."""
    root = Path(root)
    source = root / SNAPSHOT_DIR / name
    if not source.is_dir():
        raise FileNotFoundError(f"snapshot no encontrado: {name}")
    for child in root.iterdir():
        if child.name == SNAPSHOT_DIR:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink()
    for child in source.iterdir():
        target = root / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copyfile(child, target)
