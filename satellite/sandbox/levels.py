"""Selección y verificación del sandbox por nivel, con fallo cerrado.

Punto ÚNICO que traduce un nivel anunciado (``N0``/``N1``/``N2``) al runner concreto y
decide si el nivel puede aplicarse en esta máquina. El objetivo es que el código de
agente no ejecute procesos fuera del nivel declarado: cuando un nivel que se *anuncia*
como aislado no se puede aplicar, se falla cerrado (no se ejecuta nada) en vez de caer
en silencio al host.

Semántica real de cada nivel (ver también ``LevelCapabilities``):

- **N0** — subproceso con entorno limpio y timeout. Aísla el *proceso* (no hereda el
  entorno ni el directorio fuera del workspace), pero **no** aísla del sistema
  operativo: el comando corre con los permisos del usuario y **puede usar la red**.
- **N1** — contenedor Docker con ``--network none`` y el workspace montado en sólo
  lectura. Es el único nivel que confina de verdad (sistema de archivos y red). Si
  Docker no está disponible, N1 **no** degrada a N0: falla cerrado.
- **N2** — host sin aislamiento (sólo con política ``unrestricted``).

Un nivel desconocido **no** se interpreta como N0: se devuelve un runner que nunca
ejecuta.
"""

from __future__ import annotations

from dataclasses import dataclass

from satellite.sandbox.base import (
    SandboxLevel,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
)
from satellite.sandbox.docker_sandbox import DockerSandbox
from satellite.sandbox.host_sandbox import HostSandbox
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox

DEFAULT_LEVEL = "N0"

__all__ = [
    "DEFAULT_LEVEL",
    "CAPABILITIES",
    "LevelCapabilities",
    "FailClosedSandbox",
    "normalize_level",
    "capabilities_for",
    "describe",
    "enforcement_error",
    "resolve_sandbox",
    "execution_sandbox",
    "background_error",
]


@dataclass(frozen=True)
class LevelCapabilities:
    """Qué garantiza (y qué no) un nivel, para documentación y UX."""

    level: str
    os_isolation: bool
    network_isolation: bool
    filesystem_isolation: bool
    note: str

    def to_dict(self) -> dict:
        """Serializa las capacidades a dict JSON-serializable."""
        return {
            "level": self.level,
            "os_isolation": self.os_isolation,
            "network_isolation": self.network_isolation,
            "filesystem_isolation": self.filesystem_isolation,
            "note": self.note,
        }


CAPABILITIES: dict[str, LevelCapabilities] = {
    "N0": LevelCapabilities(
        level="N0",
        os_isolation=False,
        network_isolation=False,
        filesystem_isolation=False,
        note=(
            "Subproceso con entorno limpio y timeout: aísla el proceso, no el sistema "
            "operativo; el comando corre con los permisos del usuario y puede usar la red."
        ),
    ),
    "N1": LevelCapabilities(
        level="N1",
        os_isolation=True,
        network_isolation=True,
        filesystem_isolation=True,
        note=(
            "Contenedor Docker con --network none y el workspace montado en sólo "
            "lectura: es el único nivel que confina de verdad."
        ),
    ),
    "N2": LevelCapabilities(
        level="N2",
        os_isolation=False,
        network_isolation=False,
        filesystem_isolation=False,
        note="Host sin aislamiento (sólo con política unrestricted).",
    ),
}


def normalize_level(level: object) -> str:
    """Nivel en mayúsculas; vacío o basura se normalizan al nivel por defecto (N0)."""
    return str(level or "").strip().upper() or DEFAULT_LEVEL


def capabilities_for(level: object) -> LevelCapabilities | None:
    """Capacidades declaradas del nivel (``None`` si el nivel es desconocido)."""
    return CAPABILITIES.get(normalize_level(level))


class FailClosedSandbox(SandboxRunner):
    """Runner que **nunca** ejecuta: se usa cuando un nivel no se puede aplicar.

    Falla cerrado por diseño: si el nivel anunciado no es reconocido, ningún proceso
    llega al host. ``run`` devuelve siempre un error, sin lanzar excepción (el runtime
    lo ve como ``ToolResult(success=False)``).
    """

    level = SandboxLevel.N0

    def __init__(self, *, declared_level: str, reason: str) -> None:
        self.declared_level = declared_level
        self.reason = reason

    async def run(self, argv: list[str], spec: SandboxSpec) -> SandboxResult:
        """No ejecuta nada; devuelve el motivo del fallo cerrado."""
        return SandboxResult(
            exit_code=-1,
            error=f"aislamiento no aplicable ({self.declared_level}): {self.reason}",
        )


def resolve_sandbox(level: object) -> SandboxRunner:
    """Runner concreto del nivel. Un nivel desconocido devuelve ``FailClosedSandbox``.

    N1 devuelve ``DockerSandbox`` aunque Docker no esté disponible: la ausencia se
    resuelve en tiempo de ejecución (fallo cerrado), no degradando a N0.
    """
    key = normalize_level(level)
    if key == "N0":
        return SubprocessSandbox()
    if key == "N1":
        return DockerSandbox()
    if key == "N2":
        return HostSandbox()
    return FailClosedSandbox(
        declared_level=str(level or "").strip() or key,
        reason=f"nivel de sandbox desconocido: '{key}'",
    )


def enforcement_error(level: object) -> str | None:
    """Motivo por el que el nivel NO puede aplicarse aquí (``None`` si sí puede).

    No describe el nivel como seguro: sólo si el backend que necesita está disponible.
    """
    key = normalize_level(level)
    if key in ("N0", "N2"):
        return None
    if key == "N1":
        if DockerSandbox.available():
            return None
        return (
            "N1 (Docker) seleccionado pero docker no está disponible: los comandos del "
            "agente y las skills fallarán cerrado (no se ejecutan en el host)"
        )
    return f"nivel de sandbox desconocido: '{key}' (fallo cerrado)"


def describe(level: object) -> dict:
    """Descripción honesta del nivel: capacidades declaradas + si es aplicable aquí."""
    key = normalize_level(level)
    caps = capabilities_for(key)
    base = (
        caps.to_dict()
        if caps is not None
        else {
            "level": key,
            "os_isolation": False,
            "network_isolation": False,
            "filesystem_isolation": False,
            "note": "Nivel desconocido.",
        }
    )
    return {**base, "enforceable": enforcement_error(level) is None}


def execution_sandbox(tool_ctx) -> tuple[SandboxRunner | None, str | None]:
    """Devuelve ``(runner, error)`` para ejecutar un proceso desde una tool.

    - ``(runner, None)`` — hay sandbox: se ejecuta SIEMPRE por el runner (él mismo
      falla cerrado si su backend —p. ej. Docker— no está disponible).
    - ``(None, error)`` — fallo cerrado: un nivel aislado está anunciado pero no hay
      runner. NO se ejecuta nada; no se cae al host.
    - ``(None, None)`` — sin sandbox ni nivel aislado: comportamiento de siempre
      (subproceso directo, que es exactamente N0).
    """
    sandbox = getattr(tool_ctx, "sandbox", None)
    if sandbox is not None:
        return sandbox, None
    declared = str(getattr(tool_ctx, "sandbox_level", "") or "").strip()
    key = normalize_level(declared)
    if not declared or key in ("N0", "N2"):
        return None, None
    if key == "N1":
        return None, (
            "aislamiento N1 anunciado pero no hay sandbox configurado: no se ejecuta en "
            "el host (fallo cerrado)"
        )
    return None, f"nivel de sandbox desconocido '{declared}': no se ejecuta (fallo cerrado)"


def background_error(tool_ctx) -> str | None:
    """Motivo por el que un job en segundo plano NO se permite (``None`` si se permite).

    Un nivel con aislamiento del sistema de archivos (N1) no se reproduce en un job en
    segundo plano (el runner de Docker es síncrono), así que se rechaza en vez de
    lanzarlo sin confinamiento. N0/N2 sí los permiten: su aislamiento (proceso/host) es
    el mismo que en primer plano.
    """
    sandbox = getattr(tool_ctx, "sandbox", None)
    if sandbox is None:
        declared = normalize_level(getattr(tool_ctx, "sandbox_level", ""))
        if declared not in ("N0", "N2"):
            return (
                "los trabajos en segundo plano se ejecutarían sin el aislamiento "
                f"anunciado ({declared}): no se permite (fallo cerrado)"
            )
        return None
    if isinstance(sandbox, FailClosedSandbox):
        return sandbox.reason
    if getattr(sandbox, "level", None) is SandboxLevel.N1:
        return (
            "con sandbox N1 los trabajos en segundo plano se ejecutarían sin "
            "confinamiento: no se permite (usa un comando en primer plano)"
        )
    return None
