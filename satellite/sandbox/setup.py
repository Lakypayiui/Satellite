"""Detección y preparación de Docker (N1), opcional y en un solo paso."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DECISION_FILE = "sandbox.json"


@dataclass(frozen=True)
class DockerStatus:
    """Estado de Docker en la máquina."""

    available: bool
    version: str = ""
    instructions: str = ""


def detect_docker() -> DockerStatus:
    """Detecta ``docker`` y su versión (sin fallar si no está)."""
    binary = shutil.which("docker")
    if binary is None:
        return DockerStatus(
            available=False,
            instructions=setup_instructions(),
        )
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return DockerStatus(available=False, instructions=setup_instructions())
    if proc.returncode != 0:
        return DockerStatus(available=False, instructions=setup_instructions())
    return DockerStatus(available=True, version=proc.stdout.strip())


def setup_instructions() -> str:
    """Instrucciones de instalación de Docker por plataforma."""
    return (
        "Docker no está instalado (opcional, habilita el sandbox N1).\n"
        "  - Windows/macOS: instala Docker Desktop y arráncalo.\n"
        "  - Linux: 'curl -fsSL https://get.docker.com | sh' y añade tu usuario al grupo docker.\n"
        "  - Verifica con: docker --version\n"
        "Sin Docker, Satellite sigue funcionando en N0 (subproceso aislado)."
    )


def _marker_path(global_root: Path) -> Path:
    return Path(global_root) / "config" / DECISION_FILE


def docker_decision_pending(global_root: Path) -> bool:
    """True si aún no se ha preguntado al usuario por Docker (primera ejecución)."""
    return not _marker_path(global_root).exists()


def record_decision(global_root: Path, enabled: bool) -> None:
    """Guarda la decisión del usuario para no volver a preguntar en cada arranque."""
    marker = _marker_path(global_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"docker_enabled": bool(enabled)}, indent=2),
        encoding="utf-8",
    )


def read_decision(global_root: Path) -> dict | None:
    """Lee la decisión guardada (None si no existe)."""
    marker = _marker_path(global_root)
    if not marker.exists():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
