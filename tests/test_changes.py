"""Cambios por tarea: verlos y deshacerlos.

``git diff`` sólo ve lo rastreado y sólo si el proyecto es un repo, así que el registro
lo lleva el framework: cada escritura guarda la versión anterior y restaurar devuelve el
fichero a como estaba antes de la tarea.
"""

import asyncio
import json
from pathlib import Path

from satellite.core.events import EventType
from satellite.core.events import ExecutionEvent
from satellite.persistence.changes import ChangeLedger
from satellite.tools.base import ToolContext
from satellite.tools.fs import EditFileTool, WriteFileTool


def run(coro):
    return asyncio.run(coro)


def _ctx(tmp_path: Path, task_id: str = "t1") -> ToolContext:
    return ToolContext(project_root=tmp_path, task_id=task_id)


# --- el registro ---


def test_a_write_is_registered_and_restorable(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("antes\n", encoding="utf-8")
    ledger = ChangeLedger(tmp_path)

    assert ledger.record("t1", tmp_path / "a.py", "antes\n") is True
    assert ledger.files("t1") == ["a.py"]
    assert ledger.has("t1") is True

    (tmp_path / "a.py").write_text("después\n", encoding="utf-8")
    resultado = ledger.restore("t1")

    assert resultado["restored"] == ["a.py"]
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "antes\n"


def test_a_new_file_is_removed_on_restore(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path)
    ledger.record("t1", tmp_path / "nuevo.py", None)
    (tmp_path / "nuevo.py").write_text("x", encoding="utf-8")

    resultado = ledger.restore("t1")

    assert resultado["removed"] == ["nuevo.py"]
    assert not (tmp_path / "nuevo.py").exists()


def test_the_first_write_of_the_task_wins(tmp_path: Path) -> None:
    """Restaurar devuelve el fichero a como estaba ANTES de la tarea."""
    (tmp_path / "a.py").write_text("original\n", encoding="utf-8")
    ledger = ChangeLedger(tmp_path)
    ledger.record("t1", tmp_path / "a.py", "original\n")
    ledger.record("t1", tmp_path / "a.py", "intermedio\n")  # la copia buena ya está hecha

    (tmp_path / "a.py").write_text("final\n", encoding="utf-8")
    ledger.restore("t1")

    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "original\n"
    assert ledger.files("t1") == ["a.py"]


def test_the_diff_shows_what_changed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("uno\ndos\n", encoding="utf-8")
    ledger = ChangeLedger(tmp_path)
    ledger.record("t1", tmp_path / "a.py", "uno\ndos\n")
    (tmp_path / "a.py").write_text("uno\ntres\n", encoding="utf-8")

    diff = ledger.diff("t1")

    assert "-dos" in diff and "+tres" in diff
    assert "a/a.py" in diff and "b/a.py" in diff


def test_a_new_file_diff_is_all_additions(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path)
    ledger.record("t1", tmp_path / "nuevo.py", None)
    (tmp_path / "nuevo.py").write_text("print(1)\n", encoding="utf-8")

    diff = ledger.diff("t1")

    assert "+print(1)" in diff
    assert "/dev/null" in diff


def test_a_huge_previous_version_is_not_restorable(tmp_path: Path) -> None:
    from satellite.persistence.changes import MAX_BACKUP_BYTES

    ledger = ChangeLedger(tmp_path)
    ledger.record("t1", tmp_path / "grande.txt", "x" * (MAX_BACKUP_BYTES + 1))

    assert ledger.entries("t1")[0]["restorable"] is False
    resultado = ledger.restore("t1")
    assert resultado["restored"] == []
    assert resultado["skipped"] and "sin copia previa" in resultado["skipped"][0]
    assert "sin copia previa" in ledger.diff("t1")


def test_a_path_outside_the_project_is_not_registered(tmp_path: Path) -> None:
    fuera = tmp_path.parent / "fuera_del_proyecto.txt"
    ledger = ChangeLedger(tmp_path)

    assert ledger.record("t1", fuera, "x") is True  # no aplica, no es un fallo
    assert ledger.files("t1") == []


def test_a_corrupt_manifest_does_not_explode(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path)
    target = ledger.root / "t1"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("{no json", encoding="utf-8")

    assert ledger.entries("t1") == []
    assert ledger.files("t1") == []


def test_latest_task_is_the_last_one_recorded(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path)
    assert ledger.latest_task() is None
    ledger.record("t1", tmp_path / "a.py", None)
    ledger.record("t2", tmp_path / "b.py", None)
    assert ledger.latest_task() == "t2"


# --- las tools lo alimentan ---


def test_write_file_feeds_the_ledger(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("antes\n", encoding="utf-8")

    run(WriteFileTool().execute({"path": "a.py", "content": "después\n"}, _ctx(tmp_path)))

    ledger = ChangeLedger(tmp_path)
    assert ledger.files("t1") == ["a.py"]
    assert "-antes" in ledger.diff("t1")


def test_edit_file_feeds_the_ledger_and_restores(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("hola mundo\n", encoding="utf-8")

    run(
        EditFileTool().execute(
            {"path": "a.py", "old_string": "mundo", "new_string": "satellite"}, _ctx(tmp_path)
        )
    )

    ledger = ChangeLedger(tmp_path)
    assert ledger.files("t1") == ["a.py"]
    assert "+hola satellite" in ledger.diff("t1")
    ledger.restore("t1")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "hola mundo\n"


def test_without_a_task_id_nothing_is_recorded(tmp_path: Path) -> None:
    run(
        WriteFileTool().execute(
            {"path": "a.py", "content": "x"}, ToolContext(project_root=tmp_path)
        )
    )
    assert ChangeLedger(tmp_path).latest_task() is None


# --- TUI ---


def make_app(tmp_path: Path, monkeypatch):
    from satellite.tui.app import SatelliteTUI
    from satellite.tui.session import Session

    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    cfg_dir = tmp_path / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
    )
    return SatelliteTUI(Session(tmp_path, home=home))


def test_the_panel_marks_additions_and_deletions(tmp_path: Path) -> None:
    from satellite.tui import render

    ledger = ChangeLedger(tmp_path)
    ledger.record("t1", tmp_path / "a.py", "uno\ndos\n")
    (tmp_path / "a.py").write_text("uno\ntres\n", encoding="utf-8")

    texto = str(render.changes_panel("t1", ledger.files("t1"), ledger.diff("t1")).renderable)

    assert "a.py" in texto
    assert "+tres" in texto
    assert "1 archivo" in texto


def test_the_changes_command_lists_the_files(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        ledger = ChangeLedger(tmp_path)
        ledger.record("t1", tmp_path / "a.py", "uno\n")
        (tmp_path / "a.py").write_text("dos\n", encoding="utf-8")

        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_changes("")
            await pilot.pause()
            assert "a.py" in "\n".join(app.transcript)

    asyncio.run(scenario())


def test_the_undo_command_restores_the_task(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        (tmp_path / "a.py").write_text("antes\n", encoding="utf-8")
        ChangeLedger(tmp_path).record("t1", tmp_path / "a.py", "antes\n")
        (tmp_path / "a.py").write_text("después\n", encoding="utf-8")

        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_undo_task("")
            await pilot.pause()
            assert (tmp_path / "a.py").read_text(encoding="utf-8") == "antes\n"
            assert "restaurado" in "\n".join(app.transcript)

    asyncio.run(scenario())


def test_finishing_a_task_leaves_the_changes_in_the_log(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        ChangeLedger(tmp_path).record("t1", tmp_path / "a.py", "uno\n")

        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_event(
                ExecutionEvent(event_type=EventType.TASK_FINISHED, task_id="t1", payload={})
            )
            await pilot.pause()
            assert "a.py" in "\n".join(app.transcript)

    asyncio.run(scenario())


def test_without_changes_the_commands_say_so(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_changes("")
            app._cmd_undo_task("")
            await pilot.pause()
            salida = "\n".join(app.transcript)
            assert "no hay cambios registrados" in salida

    asyncio.run(scenario())
