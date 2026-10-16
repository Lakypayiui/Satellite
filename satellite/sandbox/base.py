"""Contratos del sandbox: niveles, especificación, resultado y entorno limpio."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

DEFAULT_OUTPUT_LIMIT = 64_000

# Variables de entorno mínimas para que un intérprete resuelva rutas y binarios.
# Deliberadamente **fuera**: HOME/USERPROFILE y TEMP/TMP. Un script no necesita
# conocer las carpetas del usuario y tenerlas le da alcance fuera del workspace;
# si una skill las necesita de verdad, se piden explícitas por ``extra``.
ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "SYSTEMROOT",
    "windir",
    "ComSpec",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)


class SandboxLevel(Enum):
    """Nivel de aislamiento de la ejecución."""

    N0 = "N0"
    N1 = "N1"
    N2 = "N2"


@dataclass(frozen=True)
class SandboxSpec:
    """Parámetros de una ejecución confinada."""

    project_root: Path
    level: SandboxLevel = SandboxLevel.N0
    workdir: Path | None = None
    allow_network: bool = False
    allowed_commands: tuple[str, ...] = ()
    timeout_s: int = 30
    env: dict = field(default_factory=dict)
    extra_mounts: tuple[Path, ...] = ()
    stdin: str | None = None
    # Token de cancelación de la ejecución: el sandbox es quien sabe cómo terminar lo que
    # él mismo arrancó, así que la petición viaja con el resto de los parámetros.
    cancel: object | None = None


@dataclass(frozen=True)
class SandboxResult:
    """Resultado crudo de una ejecución en sandbox."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        """True si terminó con código 0 y sin error interno."""
        return self.exit_code == 0 and not self.timed_out and self.error is None


def sanitize_env(extra: dict | None = None) -> dict[str, str]:
    """Entorno limpio: sólo variables de la allowlist + las extra indicadas."""
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def truncate(text: str, limit: int = DEFAULT_OUTPUT_LIMIT) -> str:
    """Trunca texto largo añadiendo una nota explícita."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncado: {len(text) - limit} caracteres omitidos]"


class SandboxRunner(ABC):
    """Ejecuta un comando confinado según un nivel de aislamiento."""

    level: SandboxLevel

    @abstractmethod
    async def run(self, argv: list[str], spec: SandboxSpec) -> SandboxResult:
        """Ejecuta ``argv`` y devuelve el resultado crudo."""
        raise NotImplementedError
