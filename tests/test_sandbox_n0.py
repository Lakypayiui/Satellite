"""Tests del sandbox N0, del entorno limpio y de la preparación de Docker (Fase 3)."""

import asyncio
import os
import sys
from pathlib import Path

from satellite.sandbox.base import (
    SandboxLevel,
    SandboxSpec,
    sanitize_env,
    truncate,
)
from satellite.sandbox.docker_sandbox import DockerSandbox
from satellite.sandbox.host_sandbox import HostSandbox
from satellite.sandbox.setup import (
    detect_docker,
    docker_decision_pending,
    record_decision,
    setup_instructions,
)
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox
from satellite.sandbox.levels import resolve_sandbox
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import ToolContext
from satellite.tools.terminal import RunCommandTool


def run(coro):
    return asyncio.run(coro)


def test_n0_runs_and_captures(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = run(
        sandbox.run(
            [sys.executable, "-c", "print(1 + 1)"],
            SandboxSpec(project_root=tmp_path, level=SandboxLevel.N0, timeout_s=30),
        )
    )
    assert result.success is True
    assert "2" in result.stdout


def test_run_command_uses_the_runtime_venv_python(tmp_path: Path) -> None:
    """A bare `python` in a command should be the interpreter running Satellite.

    Without this, `python -m pytest` in a venv-based runtime used the OS Python and
    failed with "No module named pytest".
    """
    import pytest

    if sys.prefix == sys.base_prefix:
        pytest.skip("el intérprete de pruebas no está en un venv")
    ctx = ToolContext(
        project_root=tmp_path,
        policy=DefaultSecurityPolicy(
            allowed_tools={"run_command"}, allow_terminal=True, allowed_commands={"python"}
        ),
        sandbox=resolve_sandbox("N0"),
        sandbox_level="N0",
    )
    result = run(
        RunCommandTool().execute(
            {"command": 'python -c "import sys; print(sys.executable)"'}, ctx
        )
    )
    assert result.success is True, result.error
    assert Path(result.output["stdout"].strip()).resolve() == Path(sys.executable).resolve()


def test_n0_timeout(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = run(
        sandbox.run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            SandboxSpec(project_root=tmp_path, level=SandboxLevel.N0, timeout_s=1),
        )
    )
    assert result.success is False
    assert result.timed_out is True


def test_n0_allowlist_denies(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = run(
        sandbox.run(
            ["python", "-c", "print(1)"],
            SandboxSpec(project_root=tmp_path, allowed_commands=("git",)),
        )
    )
    assert result.success is False
    assert "no permitido" in (result.error or "")


def test_n0_stdin(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    result = run(
        sandbox.run(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
            SandboxSpec(project_root=tmp_path, stdin="hola"),
        )
    )
    assert result.stdout.strip() == "HOLA"


def test_sanitize_env_drops_secrets_keeps_path(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "shh")
    env = sanitize_env({"EXTRA": "1"})
    assert "SECRET_TOKEN" not in env
    assert env["EXTRA"] == "1"
    if "PATH" in os.environ:
        assert env["PATH"] == os.environ["PATH"]


def test_truncate_adds_note() -> None:
    assert truncate("abc", 10) == "abc"
    out = truncate("x" * 20, 10)
    assert out.startswith("x" * 10)
    assert "truncado" in out


def test_docker_build_argv_defaults(tmp_path: Path) -> None:
    sandbox = DockerSandbox()
    argv = sandbox.build_argv(["python", "main.py"], SandboxSpec(project_root=tmp_path))
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert any(a.endswith(":ro") for a in argv)


def test_docker_build_argv_network_and_stdin(tmp_path: Path) -> None:
    sandbox = DockerSandbox()
    argv = sandbox.build_argv(
        ["python", "main.py"],
        SandboxSpec(project_root=tmp_path, allow_network=True, stdin="{}"),
    )
    assert argv[argv.index("--network") + 1] == "bridge"
    assert "-i" in argv


def test_docker_run_graceful_without_docker(tmp_path: Path, monkeypatch) -> None:
    sandbox = DockerSandbox()
    monkeypatch.setattr(DockerSandbox, "available", staticmethod(lambda: False))
    result = run(sandbox.run(["python", "main.py"], SandboxSpec(project_root=tmp_path)))
    assert result.success is False
    assert "docker no disponible" in (result.error or "")


def test_host_sandbox_runs(tmp_path: Path) -> None:
    sandbox = HostSandbox()
    result = run(
        sandbox.run([sys.executable, "-c", "print('n2')"], SandboxSpec(project_root=tmp_path))
    )
    assert result.success is True
    assert "n2" in result.stdout


def test_docker_decision_marker(tmp_path: Path) -> None:
    assert docker_decision_pending(tmp_path) is True
    record_decision(tmp_path, enabled=False)
    assert docker_decision_pending(tmp_path) is False


def test_detect_docker_returns_status() -> None:
    status = detect_docker()
    assert isinstance(status.available, bool)
    if not status.available:
        assert status.instructions == setup_instructions()
