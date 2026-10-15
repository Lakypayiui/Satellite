"""Tests de tools de filesystem (con asyncio.run, sin pytest-asyncio)."""

import asyncio
import sys
from pathlib import Path

from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import ToolContext
from satellite.tools.fs import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from satellite.tools.terminal import RunCommandTool

# El comando viaja como string y se parte con shlex en modo POSIX, que se come los
# backslashes: la ruta del intérprete se escapa y se entrecomilla para que sobreviva
# al split igual en Windows (ruta absoluta) que en Linux (donde "python" no existe).
_PYTHON = '"%s"' % str(sys.executable).replace("\\", "\\\\")


def run(coro):
    return asyncio.run(coro)


def make_ctx(tmp_path: Path, policy=None) -> ToolContext:
    return ToolContext(project_root=tmp_path, policy=policy)


def test_write_read_roundtrip(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    w = run(WriteFileTool().execute({"path": "sub/a.txt", "content": "hola"}, ctx))
    assert w.success is True
    assert w.output["bytes"] == 4
    r = run(ReadFileTool().execute({"path": "sub/a.txt"}, ctx))
    assert r.output == "hola"


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    w = run(WriteFileTool().execute({"path": "deep/nested/b.txt", "content": "x"}, ctx))
    assert w.success is True
    assert (tmp_path / "deep" / "nested" / "b.txt").exists()


def test_read_missing_file(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    r = run(ReadFileTool().execute({"path": "no_existe.txt"}, ctx))
    assert r.success is False


def test_edit_first_occurrence(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    run(WriteFileTool().execute({"path": "a.txt", "content": "uno dos uno"}, ctx))
    e = run(
        EditFileTool().execute(
            {"path": "a.txt", "old_string": "uno", "new_string": "UNO"}, ctx
        )
    )
    assert e.success is True
    assert (tmp_path / "a.txt").read_text() == "UNO dos uno"


def test_edit_missing_old_string(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    run(WriteFileTool().execute({"path": "a.txt", "content": "hola"}, ctx))
    e = run(
        EditFileTool().execute({"path": "a.txt", "old_string": "zzz", "new_string": "x"}, ctx)
    )
    assert e.success is False


def test_list_dir(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    run(WriteFileTool().execute({"path": "a.txt", "content": "1"}, ctx))
    run(WriteFileTool().execute({"path": "sub/b.txt", "content": "2"}, ctx))
    entries = run(ListDirTool().execute({"path": "."}, ctx))
    names = {e["name"] for e in entries.output}
    assert {"a.txt", "sub"} <= names
    assert not any(e["path"].endswith("b.txt") for e in entries.output)


def test_path_out_of_root_denied(tmp_path: Path) -> None:
    import pytest

    from satellite.core.errors import ToolError

    ctx = make_ctx(tmp_path, DefaultSecurityPolicy())
    # la tool lanza ToolError en errores de permiso (el registry lo convierte a ToolResult)
    with pytest.raises(ToolError, match="permiso denegado"):
        run(ReadFileTool().execute({"path": "../afuera.txt"}, ctx))


def test_terminal_exit_zero(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    t = run(RunCommandTool().execute({"command": f'{_PYTHON} -c "print(1+1)"'}, ctx))
    assert t.success is True
    assert "2" in t.output["stdout"]


def test_terminal_exit_nonzero(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    t = run(RunCommandTool().execute({"command": f'{_PYTHON} -c "import sys; sys.exit(3)"'}, ctx))
    assert t.success is False
    assert t.output["exit_code"] == 3


def test_terminal_timeout(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    t = run(
        RunCommandTool().execute(
            {"command": f'{_PYTHON} -c "import time; time.sleep(5)"', "timeout_s": 1}, ctx
        )
    )
    assert t.success is False
    assert "timeout" in (t.error or "").lower()


def test_terminal_denied_by_policy(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, DefaultSecurityPolicy())
    t = run(RunCommandTool().execute({"command": "python -c \"print(1)\""}, ctx))
    assert t.success is False
    assert "permiso denegado" in (t.error or "")
