"""Resolución de directorios de datos: globales por usuario vs. por proyecto.

El kernel (código del paquete) se mantiene separado de los datos. Aquí sólo se
resuelven rutas; la creación de directorios es explícita vía ``ensure_dirs``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ENV_HOME = "SATELLITE_HOME"
ENV_SKILLS_DIR = "SATELLITE_SKILLS_DIR"
ENV_PROJECT_ROOT = "SATELLITE_PROJECT_ROOT"

GLOBAL_DIR_NAME = ".satellite"
PROJECT_DIR_NAME = ".satellite"

GLOBAL_SUBDIRS: tuple[str, ...] = ("agents", "skills", "audit")
PROJECT_SUBDIRS: tuple[str, ...] = (
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


@dataclass(frozen=True)
class DataPaths:
    """Rutas de datos resueltas (globales y, si aplica, del proyecto)."""

    global_root: Path
    agents_root: Path
    skills_root: Path
    audit_root: Path
    project_root: Path | None = None
    project_data: Path | None = None

    def writable_roots(self) -> tuple[Path, ...]:
        """Raíces donde el runtime (agentes/skills) puede escribir."""
        roots = [self.agents_root, self.skills_root]
        if self.project_data is not None:
            roots.append(self.project_data)
        return tuple(roots)


def resolve_paths(
    project_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> DataPaths:
    """Resuelve las rutas de datos aplicando los overrides de entorno.

    - ``SATELLITE_HOME`` → raíz global (por defecto ``~/.satellite``).
    - ``SATELLITE_SKILLS_DIR`` → raíz de skills (por defecto ``<global>/skills``).
    - ``SATELLITE_PROJECT_ROOT`` → proyecto cuando no se pasa explícito.
    """
    environment: Mapping[str, str] = env if env is not None else os.environ

    home_raw = environment.get(ENV_HOME)
    global_root = (
        Path(home_raw).expanduser() if home_raw else Path.home() / GLOBAL_DIR_NAME
    )

    skills_raw = environment.get(ENV_SKILLS_DIR)
    skills_root = (
        Path(skills_raw).expanduser() if skills_raw else global_root / "skills"
    )

    proj = project_root if project_root is not None else environment.get(ENV_PROJECT_ROOT)
    resolved_project = Path(proj).expanduser().resolve() if proj is not None else None
    project_data = (
        resolved_project / PROJECT_DIR_NAME if resolved_project is not None else None
    )

    return DataPaths(
        global_root=global_root,
        agents_root=global_root / "agents",
        skills_root=skills_root,
        audit_root=global_root / "audit",
        project_root=resolved_project,
        project_data=project_data,
    )


def ensure_dirs(paths: DataPaths) -> DataPaths:
    """Crea (idempotente) los directorios globales y, si hay, los del proyecto."""
    paths.global_root.mkdir(parents=True, exist_ok=True)
    paths.agents_root.mkdir(parents=True, exist_ok=True)
    paths.skills_root.mkdir(parents=True, exist_ok=True)
    paths.audit_root.mkdir(parents=True, exist_ok=True)
    if paths.project_data is not None:
        for sub in PROJECT_SUBDIRS:
            (paths.project_data / sub).mkdir(parents=True, exist_ok=True)
    return paths
