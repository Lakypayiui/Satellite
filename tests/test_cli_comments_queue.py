"""CLI: comentarios inline y cola, reutilizando los almacenes compartidos."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from satellite.cli.app import app


def _id_de(output: str, needle: str, *, index: int = 0) -> str:
    for linea in output.splitlines():
        if needle in linea:
            return linea.split()[index]
    raise AssertionError(f"no encontré {needle!r} en:\n{output}")


def test_comments_cli_roundtrip(tmp_path: Path) -> None:
    runner = CliRunner()
    proyecto = ["--project", str(tmp_path)]

    creado = runner.invoke(
        app, ["comments", "add", "src/app.py", "3-5", "revisar esto", *proyecto]
    )
    assert creado.exit_code == 0, creado.output

    listado = runner.invoke(app, ["comments", "list", *proyecto])
    assert listado.exit_code == 0
    assert "src/app.py:3-5" in listado.output
    assert "revisar esto" in listado.output

    comment_id = _id_de(listado.output, "src/app.py:3-5")
    resuelto = runner.invoke(app, ["comments", "resolve", comment_id, *proyecto])
    assert resuelto.exit_code == 0, resuelto.output
    assert "resuelto" in resuelto.output

    abierto = runner.invoke(app, ["comments", "list", "--open", *proyecto])
    assert abierto.exit_code == 0
    assert "src/app.py" not in abierto.output

    borrado = runner.invoke(app, ["comments", "delete", comment_id, *proyecto])
    assert borrado.exit_code == 0, borrado.output
    assert "(sin comentarios)" in runner.invoke(app, ["comments", "list", *proyecto]).output


def test_comments_cli_rejects_invalid_anchor(tmp_path: Path) -> None:
    runner = CliRunner()
    proyecto = ["--project", str(tmp_path)]

    malo = runner.invoke(app, ["comments", "add", "a.py", "5-2", "x", *proyecto])
    escape = runner.invoke(app, ["comments", "add", "../x", "1", "x", *proyecto])

    assert malo.exit_code == 1
    assert escape.exit_code == 1


def test_queue_cli_roundtrip(tmp_path: Path) -> None:
    runner = CliRunner()
    proyecto = ["--project", str(tmp_path)]

    assert runner.invoke(app, ["queue", "add", "primero", *proyecto]).exit_code == 0
    assert runner.invoke(app, ["queue", "add", "segundo", "--plan", *proyecto]).exit_code == 0

    listado = runner.invoke(app, ["queue", "list", *proyecto])
    assert listado.exit_code == 0
    assert "primero" in listado.output and "segundo" in listado.output
    assert "[plan]" in listado.output

    item_id = _id_de(listado.output, "primero", index=1)
    editado = runner.invoke(app, ["queue", "edit", item_id, "primero editado", *proyecto])
    assert editado.exit_code == 0, editado.output
    assert "primero editado" in runner.invoke(app, ["queue", "list", *proyecto]).output

    quitado = runner.invoke(app, ["queue", "remove", item_id, *proyecto])
    assert quitado.exit_code == 0, quitado.output

    assert runner.invoke(app, ["queue", "clear", *proyecto]).exit_code == 0
    assert "(cola vacía)" in runner.invoke(app, ["queue", "list", *proyecto]).output
