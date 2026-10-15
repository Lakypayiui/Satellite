"""Sandbox: aislamiento de ejecución por niveles (N0 subproceso, N1 docker, N2 host)."""

from satellite.sandbox.base import (
    SandboxLevel,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
    sanitize_env,
    truncate,
)
from satellite.sandbox.docker_sandbox import DockerSandbox
from satellite.sandbox.host_sandbox import HostSandbox
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox
from satellite.sandbox.setup import (
    DockerStatus,
    detect_docker,
    docker_decision_pending,
    record_decision,
    setup_instructions,
)

__all__ = [
    "SandboxLevel",
    "SandboxSpec",
    "SandboxResult",
    "SandboxRunner",
    "sanitize_env",
    "truncate",
    "SubprocessSandbox",
    "DockerSandbox",
    "HostSandbox",
    "DockerStatus",
    "detect_docker",
    "docker_decision_pending",
    "record_decision",
    "setup_instructions",
]
