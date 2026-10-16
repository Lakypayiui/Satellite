"""Sandbox N2: host sin aislamiento (sólo con política ``unrestricted`` explícita)."""

from __future__ import annotations

from satellite.sandbox.base import (
    DEFAULT_OUTPUT_LIMIT,
    SandboxLevel,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
    truncate,
)
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox


class HostSandbox(SandboxRunner):
    """Nivel N2: ejecuta en el host, sin allowlist ni confinamiento de red.

    Debe habilitarse explícitamente (``unrestricted``); sigue sujeto al guard
    de escritura del kernel y a la auditoría.
    """

    level = SandboxLevel.N2

    def __init__(self, output_limit: int = DEFAULT_OUTPUT_LIMIT) -> None:
        self._delegate = SubprocessSandbox(output_limit=output_limit)

    async def run(self, argv: list[str], spec: SandboxSpec) -> SandboxResult:
        # Sin allowlist de comandos: hereda el comportamiento N0 pero sin filtro.
        wide = SandboxSpec(
            project_root=spec.project_root,
            level=SandboxLevel.N2,
            workdir=spec.workdir,
            allow_network=True,
            allowed_commands=(),
            timeout_s=spec.timeout_s,
            env=spec.env,
            extra_mounts=spec.extra_mounts,
            cancel=spec.cancel,
        )
        return await self._delegate.run(argv, wide)
