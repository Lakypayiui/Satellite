"""Sandbox N0: subproceso con directorio restringido, timeout y entorno limpio."""

from __future__ import annotations

import asyncio

from satellite.sandbox.base import (
    DEFAULT_OUTPUT_LIMIT,
    SandboxLevel,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
    sanitize_env,
    truncate,
)


class SubprocessSandbox(SandboxRunner):
    """Nivel N0: subproceso sin red por defecto y con allowlist de comandos."""

    level = SandboxLevel.N0

    def __init__(self, output_limit: int = DEFAULT_OUTPUT_LIMIT) -> None:
        self.output_limit = output_limit

    async def run(self, argv: list[str], spec: SandboxSpec) -> SandboxResult:
        if not argv:
            return SandboxResult(exit_code=-1, error="argv vacío")
        if spec.allowed_commands and argv[0] not in spec.allowed_commands:
            return SandboxResult(
                exit_code=-1,
                error=f"comando no permitido: {argv[0]}",
            )

        cwd = spec.workdir or spec.project_root
        env = sanitize_env(spec.env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE if spec.stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            payload = spec.stdin.encode("utf-8") if spec.stdin is not None else None
            from satellite.orchestration.cancel import race_cancel

            try:
                salida, cancelado = await race_cancel(
                    spec.cancel,
                    asyncio.wait_for(proc.communicate(input=payload), timeout=spec.timeout_s),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return SandboxResult(
                    exit_code=-1,
                    timed_out=True,
                    error=f"timeout de {spec.timeout_s}s alcanzado",
                )
            if cancelado:
                proc.kill()
                await proc.wait()
                motivo = str(getattr(spec.cancel, "reason", "") or "cancelado por el usuario")
                return SandboxResult(
                    exit_code=-1,
                    cancelled=True,
                    error=f"cancelado: {motivo}",
                )
            stdout, stderr = salida
        except (OSError, ValueError) as exc:
            return SandboxResult(exit_code=-1, error=f"no se pudo ejecutar: {exc}")

        return SandboxResult(
            exit_code=proc.returncode or 0,
            stdout=truncate(stdout.decode(errors="replace"), self.output_limit),
            stderr=truncate(stderr.decode(errors="replace"), self.output_limit),
        )
