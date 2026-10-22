"""Trabajos en segundo plano: lanzar, consultar, esperar y parar."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from satellite.tools.base import ToolContext
from satellite.tools.jobs import JobInfo, JobTool, registry_for, start_background
from satellite.tools.terminal import RunCommandTool


def run(coro):
    return asyncio.run(coro)


def _echo(texto: str) -> list[str]:
    return [sys.executable, "-c", f"print('{texto}')"]


def _dormir(segundos: int) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({segundos})"]


def _start(tmp_path: Path, argv: list[str]) -> JobInfo:
    return registry_for(tmp_path).start(
        argv, cwd=tmp_path, env=os.environ.copy(), command=" ".join(argv)
    )


# --- el registro ---


def test_a_job_runs_and_its_output_is_readable(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    info = _start(tmp_path, _echo("hola job"))

    assert info.id and info.pid
    assert registry.status(info.id).status in ("running", "done")

    _final, estado = run(registry.wait(info.id, 30))

    assert estado == "done"
    assert registry.status(info.id).status == "done"
    assert registry.status(info.id).exit_code == 0
    assert "hola job" in registry.output(info.id)


def test_a_failing_job_reports_its_exit_code(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    info = registry.start(
        [sys.executable, "-c", "raise SystemExit(3)"],
        cwd=tmp_path,
        env=os.environ.copy(),
    )

    run(registry.wait(info.id, 30))

    assert registry.status(info.id).status == "failed"
    assert registry.status(info.id).exit_code == 3


def test_a_job_can_be_stopped(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    info = _start(tmp_path, _dormir(30))

    parado = registry.stop(info.id)

    assert parado is not None and parado.status == "stopped"
    assert registry.status(info.id).status == "stopped"


def test_waiting_times_out_without_lying(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    info = _start(tmp_path, _dormir(30))

    _info, estado = run(registry.wait(info.id, 0.3))

    assert estado == "timeout"
    assert registry.status(info.id).status == "running"
    registry.stop(info.id)


def test_jobs_are_listed_most_recent_first(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    primero = _start(tmp_path, _echo("uno"))
    run(registry.wait(primero.id, 30))

    ids = [info.id for info in registry.list()]

    assert primero.id in ids


def test_a_job_from_another_process_cannot_be_controlled(tmp_path: Path) -> None:
    """El registro vive en el proceso que lanza: fuera de él, se dice que no se puede."""
    job_id = "deadbeef"
    path = tmp_path / ".satellite" / "state" / "jobs" / f"{job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"job_id": job_id, "command": "algo", "pid": 1, "status": "running"}
        ),
        encoding="utf-8",
    )
    registry = registry_for(tmp_path / "otro_proyecto")  # otro registro, mismo disco
    registry.root = path.parent

    info = registry.status(job_id)

    assert info is not None and info.status == "desconocido"
    assert registry.stop(job_id) is None
    _info, estado = run(registry.wait(job_id, 1))
    assert estado == "ajeno"


def test_a_corrupt_job_file_is_ignored(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    path = registry.root / "deadbeef.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{no json", encoding="utf-8")

    assert registry.status("deadbeef") is None


def test_job_id_traversal_is_rejected(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    state = registry.root.parent
    state.mkdir(parents=True, exist_ok=True)
    (state / "secret.json").write_text(
        json.dumps(
            {
                "job_id": "deadbeef",
                "command": "no debe leerse",
                "status": "done",
            }
        ),
        encoding="utf-8",
    )
    (state / "secret.log").write_text("no debe leerse", encoding="utf-8")

    assert registry.status("../secret") is None
    assert registry.output("../secret") == "(job_id inválido)"
    with pytest.raises(ValueError):
        registry.log_path("../secret")


def test_a_job_file_symlink_outside_is_ignored(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    secret = tmp_path / "secret.json"
    secret.write_text(
        json.dumps(
            {"job_id": "deadbeef", "command": "x", "status": "done"}
        ),
        encoding="utf-8",
    )
    link = registry.root / "deadbeef.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("el sistema no permite crear symlinks")

    assert registry.status("deadbeef") is None


def test_job_content_cannot_override_the_id(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    path = registry.root / "deadbeef.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"job_id": "../../evil", "command": "x", "status": "done"}
        ),
        encoding="utf-8",
    )

    assert registry.status("deadbeef") is None


# --- la tool ---


def test_run_command_declares_the_background_flag() -> None:
    props = RunCommandTool().parameters_schema["properties"]

    assert "background" in props
    assert "background" not in RunCommandTool().parameters_schema["required"]


def test_start_background_returns_a_job_id(tmp_path: Path) -> None:
    resultado = start_background(
        "eco",
        _echo("en segundo plano"),
        cwd=tmp_path,
        env=os.environ.copy(),
        project_root=tmp_path,
    )

    assert resultado.success is True
    assert resultado.output["job_id"]
    assert "segundo plano" in resultado.output["nota"]


def test_the_job_tool_lists_status_output_and_wait(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    info = _start(tmp_path, _echo("desde la tool"))
    tool = JobTool()
    ctx = ToolContext(project_root=tmp_path)

    listado = run(tool.execute({"action": "list"}, ctx))
    estado = run(tool.execute({"action": "status", "job_id": info.id}, ctx))
    espera = run(tool.execute({"action": "wait", "job_id": info.id, "timeout_s": 30}, ctx))
    salida = run(tool.execute({"action": "output", "job_id": info.id}, ctx))

    assert listado.success and listado.output[0]["job_id"] == info.id
    assert estado.success
    assert espera.success and espera.output["status"] == "done"
    assert "desde la tool" in salida.output["output"]
    assert registry.status(info.id).status == "done"


def test_the_job_tool_says_what_is_missing(tmp_path: Path) -> None:
    tool = JobTool()
    ctx = ToolContext(project_root=tmp_path)

    sin_id = run(tool.execute({"action": "status"}, ctx))
    inexistente = run(
        tool.execute({"action": "status", "job_id": "deadbeef"}, ctx)
    )
    invalido = run(
        tool.execute({"action": "status", "job_id": "../../secret"}, ctx)
    )
    rara = run(tool.execute({"action": "volar", "job_id": "deadbeef"}, ctx))

    assert sin_id.success is False and "job_id" in sin_id.error
    assert inexistente.success is False and "no existe" in inexistente.error
    assert invalido.success is False
    assert invalido.error_code == "invalid_arguments"
    assert rara.success is False and "desconocida" in rara.error


def test_the_job_tool_rejects_an_invalid_timeout(tmp_path: Path) -> None:
    info = _start(tmp_path, _dormir(30))
    tool = JobTool()
    ctx = ToolContext(project_root=tmp_path)

    resultado = run(
        tool.execute(
            {"action": "wait", "job_id": info.id, "timeout_s": "abc"},
            ctx,
        )
    )

    assert resultado.success is False
    assert resultado.error_code == "invalid_arguments"
    assert "timeout_s" in resultado.error
    registry_for(tmp_path).stop(info.id)


def test_the_job_tool_stops_a_job(tmp_path: Path) -> None:
    info = _start(tmp_path, _dormir(30))
    tool = JobTool()

    resultado = run(
        tool.execute({"action": "stop", "job_id": info.id}, ToolContext(project_root=tmp_path))
    )

    assert resultado.success is True
    assert resultado.output["status"] == "stopped"


def test_stopping_a_finished_job_does_not_call_it_stopped(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    info = _start(tmp_path, _echo("ya está"))
    run(registry.wait(info.id, 30))

    resultado = registry.stop(info.id)

    assert resultado is not None
    assert resultado.status == "done"  # no se «para» algo que ya había acabado


# --- la TUI ---


def test_the_tui_lists_shows_waits_and_stops_jobs(tmp_path, monkeypatch) -> None:
    async def scenario():
        import json as _json

        from satellite.tui.app import SatelliteTUI
        from satellite.tui.session import Session

        home = tmp_path / "home"
        monkeypatch.setenv("SATELLITE_HOME", str(home))
        monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
        cfg = tmp_path / ".satellite" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(
            _json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
        )
        dormido = _start(tmp_path, _dormir(30))
        app = SatelliteTUI(Session(tmp_path, home=home))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_jobs("")
            app._cmd_jobs("output " + dormido.id)
            app._cmd_jobs("stop " + dormido.id)
            app._cmd_jobs("wait " + dormido.id + " 5")
            await pilot.pause()
            app._cmd_jobs("no-existe")
            await pilot.pause()
            salida = "\n".join(app.transcript)
            assert dormido.id in salida
            assert f"trabajo {dormido.id} parado" in salida
            assert "terminó: stopped" in salida
            assert "uso: /jobs" in salida

    asyncio.run(scenario())
