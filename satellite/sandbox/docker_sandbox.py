"""Sandbox N1: contenedor Docker sin red y con el workspace montado en solo-lectura."""

from __future__ import annotations

import asyncio
import shutil

from satellite.sandbox.base import (
    DEFAULT_OUTPUT_LIMIT,
    SandboxLevel,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
    sanitize_env,
    truncate,
)

WORKSPACE_MOUNT = "/workspace"


class DockerSandbox(SandboxRunner):
    """Nivel N1: ``docker run --rm --network none`` con workspace read-only.

    Docker no es requisito: si no está disponible, ``run`` devuelve un error
    claro en lugar de fallar de forma silenciosa.
    """

    level = SandboxLevel.N1

    def __init__(self, image: str = "python:3.12-slim", output_limit: int = DEFAULT_OUTPUT_LIMIT) -> None:
        self.image = image
        self.output_limit = output_limit

    @staticmethod
    def available() -> bool:
        """True si el binario ``docker`` está en el PATH."""
        return shutil.which("docker") is not None

    def build_argv(self, argv: list[str], spec: SandboxSpec) -> list[str]:
        """Construye la invocación de ``docker run`` (pura, testeable)."""
        workdir = spec.workdir or spec.project_root
        network = "bridge" if spec.allow_network else "none"
        docker_argv = [
            "docker",
            "run",
            "--rm",
        ]
        if spec.stdin is not None:
            docker_argv.append("-i")
        docker_argv += [
            "--network",
            network,
            "-v",
            f"{workdir}:{WORKSPACE_MOUNT}:ro",
            "-w",
            WORKSPACE_MOUNT,
        ]
        for mount in spec.extra_mounts:
            docker_argv += ["-v", f"{mount}:{mount}:ro"]
        docker_argv.append(self.image)
        docker_argv.extend(argv)
        return docker_argv

    async def run(self, argv: list[str], spec: SandboxSpec) -> SandboxResult:
        if not self.available():
            return SandboxResult(
                exit_code=-1,
                error="docker no disponible: instala/habilita Docker (N1) o usa N0",
            )
        docker_argv = self.build_argv(argv, spec)
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_argv,
                env=sanitize_env(),
                stdin=asyncio.subprocess.PIPE if spec.stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            payload = spec.stdin.encode("utf-8") if spec.stdin is not None else None
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=payload), timeout=spec.timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return SandboxResult(
                    exit_code=-1,
                    timed_out=True,
                    error=f"timeout de {spec.timeout_s}s alcanzado",
                )
        except (OSError, ValueError) as exc:
            return SandboxResult(exit_code=-1, error=f"docker falló: {exc}")

        return SandboxResult(
            exit_code=proc.returncode or 0,
            stdout=truncate(stdout.decode(errors="replace"), self.output_limit),
            stderr=truncate(stderr.decode(errors="replace"), self.output_limit),
        )
