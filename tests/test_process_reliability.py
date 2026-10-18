"""Punto 3 (fiabilidad) — timeout real de procesos y terminación del árbol.

Comprueba que un proceso colgado se termina de verdad (no se espera a que acabe), que
sus **descendientes** también mueren, que el código de salida se devuelve tipado, que la
cancelación corta el proceso en marcha y que un sandbox no disponible falla cerrado (sin
caer al host). Todo se ejecuta sin shell y con el runner compartido del sandbox.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from satellite.core.tool_models import ToolResult
from satellite.sandbox.base import SandboxSpec
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox
from satellite.tools.base import ToolContext
from satellite.tools.jobs import registry_for
from satellite.tools.terminal import RunCommandTool, execute_argv


def run(coro):
    return asyncio.run(coro)


_GRANDCHILD = (
    "import os, subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', "
    "\"import os, time; time.sleep(3); open(os.environ['MARKER'], 'w').write('x')\"]); "
    "time.sleep(30)"
)


def _env_with_marker(marker: Path) -> dict:
    env = os.environ.copy()
    env["MARKER"] = str(marker)
    return env


# --- timeout real --------------------------------------------------------------


def test_a_hanging_command_is_terminated_on_timeout(tmp_path: Path) -> None:
    inicio = time.monotonic()
    resultado = run(
        execute_argv(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout_s=1,
        )
    )
    tardanza = time.monotonic() - inicio

    assert resultado.success is False
    assert "timeout" in (resultado.error or "")
    assert tardanza < 8  # no esperó a los 30 s del comando


def test_a_timeout_does_not_hang_reading_a_process_that_keeps_its_pipes(tmp_path: Path) -> None:
    """El proceso (y su descendiente) mantienen las tuberías abiertas: no debe colgarse."""
    marker = tmp_path / "nunca.txt"
    env = _env_with_marker(marker)
    inicio = time.monotonic()
    resultado = run(
        execute_argv([sys.executable, "-c", _GRANDCHILD], cwd=tmp_path, env=env, timeout_s=1)
    )
    tardanza = time.monotonic() - inicio

    assert resultado.success is False
    assert "timeout" in (resultado.error or "")
    assert tardanza < 8
    # El nieto escribiría a los ~3 s si siguiera vivo: se espera y se comprueba que no.
    time.sleep(3.5)
    assert not marker.exists()


def test_the_subprocess_sandbox_terminates_the_tree_too(tmp_path: Path) -> None:
    marker = tmp_path / "sandbox_nieto.txt"
    sandbox = SubprocessSandbox()

    async def escenario():
        spec_env = os.environ.copy()
        spec_env["MARKER"] = str(marker)
        return await sandbox.run(
            [sys.executable, "-c", _GRANDCHILD],
            SandboxSpec(project_root=tmp_path, timeout_s=1, env=spec_env),
        )

    resultado = run(escenario())

    assert resultado.success is False
    assert resultado.timed_out is True
    time.sleep(3.5)
    assert not marker.exists()


# --- salida tipada -------------------------------------------------------------


def test_a_successful_command_reports_stdout(tmp_path: Path) -> None:
    resultado = run(
        execute_argv(
            [sys.executable, "-c", "print('hola')"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout_s=10,
        )
    )

    assert resultado.success is True
    assert resultado.output["exit_code"] == 0
    assert "hola" in resultado.output["stdout"]


def test_a_failing_command_reports_the_exit_code_and_stderr(tmp_path: Path) -> None:
    resultado = run(
        execute_argv(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(7)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout_s=10,
        )
    )

    assert resultado.success is False
    assert resultado.output["exit_code"] == 7
    assert "boom" in resultado.output["stderr"]


def test_a_command_that_cannot_start_is_reported_not_raised(tmp_path: Path) -> None:
    resultado = run(
        execute_argv(
            ["binario-que-no-existe-xyz"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout_s=5,
        )
    )

    assert resultado.success is False
    assert "no se pudo ejecutar" in (resultado.error or "")


# --- cancelación ---------------------------------------------------------------


def test_cancelling_terminates_the_running_process(tmp_path: Path) -> None:
    from satellite.orchestration.cancel import CancelToken

    token = CancelToken()

    async def escenario():
        tarea = asyncio.create_task(
            execute_argv(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                env=os.environ.copy(),
                timeout_s=60,
                cancel=token,
            )
        )
        await asyncio.sleep(0.4)
        inicio = time.monotonic()
        token.request("cortado en la prueba")
        resultado = await asyncio.wait_for(tarea, timeout=10)
        return resultado, time.monotonic() - inicio

    resultado, tardanza = run(escenario())

    assert resultado.success is False
    assert resultado.error_code == "cancelled"
    assert tardanza < 5


# --- sandbox no disponible: falla cerrado, sin tocar el host --------------------


def test_run_command_fails_closed_without_running_on_the_host(tmp_path: Path, monkeypatch) -> None:
    marca = tmp_path / "host.txt"
    llamadas: list[int] = []

    def _no_debe_ejecutar(*_a, **_k):
        llamadas.append(1)
        marca.write_text("x", encoding="utf-8")
        return ToolResult(success=True)

    monkeypatch.setattr("satellite.tools.terminal.execute_argv", _no_debe_ejecutar)

    ctx = ToolContext(project_root=tmp_path, sandbox_level="N1")
    resultado = run(RunCommandTool().execute({"command": "echo hola"}, ctx))

    assert resultado.success is False
    assert resultado.error_code == "sandbox_unavailable"
    assert llamadas == []  # ni siquiera se intentó ejecutar
    assert not marca.exists()


# --- jobs: parar termina también a los descendientes ---------------------------


def test_stopping_a_job_kills_its_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "job_nieto.txt"
    env = _env_with_marker(marker)
    registry = registry_for(tmp_path)
    info = registry.start(
        [sys.executable, "-c", _GRANDCHILD], cwd=tmp_path, env=env, command="job largo"
    )
    time.sleep(0.6)  # que le dé tiempo a lanzar al nieto

    parado = registry.stop(info.id)

    assert parado is not None and parado.status == "stopped"
    time.sleep(3.5)
    assert not marker.exists()


def test_a_plain_job_still_stops_normally(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    info = registry.start(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env=os.environ.copy(),
    )

    parado = registry.stop(info.id)

    assert parado is not None and parado.status == "stopped"
    assert registry.status(info.id).status == "stopped"
