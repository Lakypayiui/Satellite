"""Aislamiento de agentes/procesos (Punto 2 de la auditoría).

Cubre lo que el runtime REALMENTE aísla y lo que no:

- el nivel anunciado (N0/N1/N2) se traduce a un runner y decide el aislamiento;
- un nivel anunciado como aislado que no se puede aplicar (N1 sin Docker) **falla
  cerrado**: no se ejecuta nada, y nunca se cae en silencio al host;
- los procesos del agente (``run_command``/``run_argv``/``run_python``) pasan por el
  sandbox configurado;
- los límites de rutas (fuera del proyecto) siguen vivos;
- el modo normal aprobado (N0, binario concedido) sigue funcionando igual;
- el sandbox no filtra secretos por el entorno.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from satellite.core.errors import PathOutsideProject
from satellite.core.tool_models import ToolResult
from satellite.sandbox.base import SandboxSpec
from satellite.sandbox.docker_sandbox import DockerSandbox
from satellite.sandbox.host_sandbox import HostSandbox
from satellite.sandbox.levels import (
    FailClosedSandbox,
    background_error,
    capabilities_for,
    describe,
    enforcement_error,
    execution_sandbox,
    normalize_level,
    resolve_sandbox,
)
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import ToolContext
from satellite.tools.fs import ReadFileTool
from satellite.tools.primitives import RunPythonTool
from satellite.tools.terminal import RunCommandTool


def run(coro):
    return asyncio.run(coro)


# --- el nivel se traduce a un runner real -------------------------------------


def test_resolve_sandbox_maps_levels_to_runners() -> None:
    assert isinstance(resolve_sandbox("N0"), SubprocessSandbox)
    assert isinstance(resolve_sandbox("N1"), DockerSandbox)
    assert isinstance(resolve_sandbox("N2"), HostSandbox)
    assert isinstance(resolve_sandbox(""), SubprocessSandbox)  # por defecto N0


def test_an_unknown_level_is_not_silently_n0() -> None:
    sandbox = resolve_sandbox("N9")
    assert isinstance(sandbox, FailClosedSandbox)
    # Y ese runner nunca ejecuta: devuelve error, no lanza nada.
    resultado = run(sandbox.run([sys.executable, "-c", "print(1)"], SandboxSpec(project_root=Path.cwd())))
    assert resultado.success is False
    assert "desconocido" in (resultado.error or "")
    assert "N9" in (resultado.error or "")


def test_normalize_level_is_case_insensitive() -> None:
    assert normalize_level("n1") == "N1"
    assert normalize_level(None) == "N0"


# --- capacidades honestas (qué sí y qué no) ------------------------------------


def test_capabilities_do_not_overpromise() -> None:
    n0 = capabilities_for("N0")
    n1 = capabilities_for("N1")
    n2 = capabilities_for("N2")
    assert n0 is not None and n0.os_isolation is False and n0.network_isolation is False
    assert n1 is not None and n1.os_isolation is True and n1.network_isolation is True
    assert n1.filesystem_isolation is True
    assert n2 is not None and n2.os_isolation is False
    assert capabilities_for("n1").network_isolation is True  # case-insensitive
    assert capabilities_for("raro") is None


def test_enforcement_error_reports_unavailable_docker(monkeypatch) -> None:
    assert enforcement_error("N0") is None
    assert enforcement_error("N2") is None
    assert enforcement_error("raro") is not None

    monkeypatch.setattr(DockerSandbox, "available", staticmethod(lambda: True))
    assert enforcement_error("N1") is None
    monkeypatch.setattr(DockerSandbox, "available", staticmethod(lambda: False))
    assert "no está disponible" in (enforcement_error("N1") or "")


def test_describe_flags_enforceability(monkeypatch) -> None:
    monkeypatch.setattr(DockerSandbox, "available", staticmethod(lambda: False))
    assert describe("N1")["enforceable"] is False
    assert describe("N0")["enforceable"] is True
    assert describe("N0")["os_isolation"] is False


# --- la costura ejecución/tool ------------------------------------------------


def test_execution_sandbox_uses_the_wired_runner(tmp_path: Path) -> None:
    sandbox = SubprocessSandbox()
    ctx = ToolContext(project_root=tmp_path, sandbox=sandbox)
    runner, error = execution_sandbox(ctx)
    assert runner is sandbox and error is None


def test_execution_sandbox_allows_legacy_host_when_no_level(tmp_path: Path) -> None:
    assert execution_sandbox(ToolContext(project_root=tmp_path)) == (None, None)
    assert execution_sandbox(ToolContext(project_root=tmp_path, sandbox_level="N0")) == (None, None)
    assert execution_sandbox(ToolContext(project_root=tmp_path, sandbox_level="N2")) == (None, None)


def test_execution_sandbox_fails_closed_when_isolated_level_has_no_runner(tmp_path: Path) -> None:
    runner, error = execution_sandbox(ToolContext(project_root=tmp_path, sandbox_level="N1"))
    assert runner is None and error is not None and "N1" in error
    runner, error = execution_sandbox(ToolContext(project_root=tmp_path, sandbox_level="N9"))
    assert runner is None and error is not None and "desconocido" in error


def test_background_is_refused_under_isolated_levels(tmp_path: Path) -> None:
    assert background_error(ToolContext(project_root=tmp_path)) is None
    assert background_error(ToolContext(project_root=tmp_path, sandbox=SubprocessSandbox())) is None
    assert background_error(ToolContext(project_root=tmp_path, sandbox=HostSandbox())) is None
    assert "N1" in (background_error(ToolContext(project_root=tmp_path, sandbox=DockerSandbox())) or "")
    assert "N1" in (background_error(ToolContext(project_root=tmp_path, sandbox_level="N1")) or "")


# --- límites de proceso: falla cerrado, sin tocar el host ---------------------


def test_run_command_fails_closed_when_isolated_level_has_no_sandbox(tmp_path: Path, monkeypatch) -> None:
    """N1 anunciado sin runner: se rechaza ANTES de intentar ejecutar nada."""
    llamadas: list[int] = []
    monkeypatch.setattr(
        "satellite.tools.terminal.execute_argv",
        lambda *a, **k: llamadas.append(1) or ToolResult(success=True),
    )
    result = run(
        RunCommandTool().execute(
            {"command": "echo hola"}, ToolContext(project_root=tmp_path, sandbox_level="N1")
        )
    )
    assert result.success is False
    assert result.error_code == "sandbox_unavailable"
    assert llamadas == []


def test_run_python_fails_closed_without_running_code(tmp_path: Path) -> None:
    """La prueba de fuego: el código del agente no llega a ejecutarse."""
    marca = tmp_path / "pwn.txt"
    ctx = ToolContext(project_root=tmp_path, sandbox_level="N1")
    result = run(RunPythonTool().execute({"code": f"open(r'{marca}', 'w').close()"}, ctx))
    assert result.success is False
    assert result.error_code == "sandbox_unavailable"
    assert not marca.exists()


def test_docker_unavailable_fails_closed_instead_of_host(tmp_path: Path, monkeypatch) -> None:
    """N1 sin Docker: la tool falla; NO se ejecuta en el host."""
    monkeypatch.setattr(DockerSandbox, "available", staticmethod(lambda: False))
    marca = tmp_path / "pwn2.txt"
    ctx = ToolContext(project_root=tmp_path, sandbox=DockerSandbox())
    result = run(RunPythonTool().execute({"code": f"open(r'{marca}', 'w').close()"}, ctx))
    assert result.success is False
    assert result.error_code == "sandbox_unavailable"
    assert "docker" in (result.error or "").lower()
    assert not marca.exists()


def test_background_under_n1_is_refused_by_the_tool(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path, sandbox=DockerSandbox())
    result = run(RunCommandTool().execute({"command": "echo hola", "background": True}, ctx))
    assert result.success is False
    assert "segundo plano" in (result.error or "")


# --- límites de rutas: fuera del proyecto -------------------------------------


def test_run_command_rejects_a_cwd_outside_the_project(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(
        allowed_tools={"run_command"}, allow_terminal=True, allowed_commands=(sys.executable,)
    )
    ctx = ToolContext(project_root=tmp_path, policy=policy)
    result = run(
        RunCommandTool().execute(
            {"command": f"{sys.executable} -c pass", "cwd_relative": ".."}, ctx
        )
    )
    assert result.success is False
    assert "denegado" in (result.error or "")


def test_read_file_rejects_a_path_outside_the_project(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(allowed_tools={"read_file"})
    ctx = ToolContext(project_root=tmp_path, policy=policy)
    fuera = tmp_path.parent / "secreto.txt"
    with pytest.raises(PathOutsideProject):
        run(ReadFileTool().execute({"path": str(fuera)}, ctx))


# --- se protege el comportamiento aprobado -----------------------------------


def test_the_wired_n0_sandbox_runs_an_approved_command(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(
        allowed_tools={"run_python"},
        allow_terminal=True,
        allowed_commands=(sys.executable,),
    )
    ctx = ToolContext(project_root=tmp_path, workdir=tmp_path, policy=policy, sandbox=SubprocessSandbox())
    result = run(RunPythonTool().execute({"code": "print(6 * 7)", "timeout_s": 30}, ctx))
    assert result.success is True
    assert "42" in result.output["stdout"]


def test_without_a_sandbox_the_normal_mode_is_unchanged(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(
        allowed_tools={"run_python"},
        allow_terminal=True,
        allowed_commands=(sys.executable,),
    )
    ctx = ToolContext(project_root=tmp_path, policy=policy)
    result = run(RunPythonTool().execute({"code": "print('hola')"}, ctx))
    assert result.success is True
    assert "hola" in result.output["stdout"]


def test_a_denied_command_stays_denied_even_with_a_sandbox(tmp_path: Path) -> None:
    """El sandbox no relaja la política: sin allowlist, el comando se rechaza."""
    policy = DefaultSecurityPolicy(allowed_tools={"run_command"}, allow_terminal=True)
    ctx = ToolContext(project_root=tmp_path, policy=policy, sandbox=SubprocessSandbox())
    result = run(RunCommandTool().execute({"command": f"{sys.executable} -c pass"}, ctx))
    assert result.success is False
    assert result.error_code == "command_not_allowed"


# --- secretos ------------------------------------------------------------------


def test_the_sandbox_env_does_not_leak_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_TEST_SECRET", "shh")
    policy = DefaultSecurityPolicy(
        allowed_tools={"run_python"},
        allow_terminal=True,
        allowed_commands=(sys.executable,),
    )
    ctx = ToolContext(project_root=tmp_path, policy=policy, sandbox=SubprocessSandbox())
    code = "import os; print(os.environ.get('SATELLITE_TEST_SECRET', 'AUSENTE'))"
    result = run(RunPythonTool().execute({"code": code}, ctx))
    assert result.success is True
    assert "AUSENTE" in result.output["stdout"]


# --- el runtime arma el sandbox del nivel configurado -------------------------


def _write_config(project: Path, payload: dict) -> None:
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_runtime_builder_wires_the_configured_sandbox(tmp_path: Path, monkeypatch) -> None:
    from satellite.cli.runtime_builder import build_runtime

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    _write_config(tmp_path, {"name": "fake", "model": "m", "sandbox_level": "N0"})
    orch, _bus, _store, _reg = build_runtime(tmp_path)
    assert isinstance(orch.services.sandbox, SubprocessSandbox)
    # El mismo sandbox llega a los agentes (factory → LLMAgent → ToolContext).
    assert isinstance(orch.services.factory.sandbox, SubprocessSandbox)

    _write_config(tmp_path, {"name": "fake", "model": "m", "sandbox_level": "N1"})
    orch2, _bus2, _store2, _reg2 = build_runtime(tmp_path)
    assert isinstance(orch2.services.sandbox, DockerSandbox)

    _write_config(tmp_path, {"name": "fake", "model": "m", "sandbox_level": "N9"})
    orch3, _bus3, _store3, _reg3 = build_runtime(tmp_path)
    assert isinstance(orch3.services.sandbox, FailClosedSandbox)
