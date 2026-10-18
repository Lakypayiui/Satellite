"""Sandbox N0: subproceso con directorio restringido, timeout y entorno limpio."""

from __future__ import annotations

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
    """Nivel N0: subproceso con entorno limpio y allowlist, sin aislamiento de red."""

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

        from satellite.sandbox.process import run_process

        cwd = spec.workdir or spec.project_root
        env = sanitize_env(spec.env)
        try:
            outcome = await run_process(
                argv,
                cwd=cwd,
                env=env,
                timeout_s=spec.timeout_s,
                stdin=spec.stdin,
                cancel=spec.cancel,
            )
        except (OSError, ValueError) as exc:
            return SandboxResult(exit_code=-1, error=f"no se pudo ejecutar: {exc}")

        if outcome.timed_out:
            return SandboxResult(
                exit_code=-1,
                timed_out=True,
                error=f"timeout de {spec.timeout_s}s alcanzado",
            )
        if outcome.cancelled:
            motivo = str(getattr(spec.cancel, "reason", "") or "cancelado por el usuario")
            return SandboxResult(
                exit_code=-1,
                cancelled=True,
                error=f"cancelado: {motivo}",
            )
        return SandboxResult(
            exit_code=outcome.exit_code,
            stdout=truncate(outcome.stdout.decode(errors="replace"), self.output_limit),
            stderr=truncate(outcome.stderr.decode(errors="replace"), self.output_limit),
        )
