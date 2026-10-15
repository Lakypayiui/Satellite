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

    from satellite.core.errors import PathOutsideProject

    ctx = make_ctx(tmp_path, DefaultSecurityPolicy())
    # La tool lanza PathOutsideProject: el error nombra la ruta **resuelta** y el
    # proyecto, y trae código para que el runtime pueda pedir permiso sin parsear texto.
    with pytest.raises(PathOutsideProject, match="fuera del proyecto"):
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
    t = run(RunCommandTool().execute({"command": f'{_PYTHON} -c "print(1)"'}, ctx))
    assert t.success is False
    assert "permiso denegado" in (t.error or "")


def test_path_outside_says_exactly_where_it_is(tmp_path: Path) -> None:
    """La negativa lleva la ruta ya resuelta y el proyecto, sin parsear texto."""
    import pytest

    from satellite.core.errors import PathOutsideProject

    ctx = make_ctx(tmp_path, DefaultSecurityPolicy())
    fuera = tmp_path.parent / "otra_carpeta" / "x.txt"
    with pytest.raises(PathOutsideProject) as excinfo:
        run(ReadFileTool().execute({"path": str(fuera)}, ctx))
    assert excinfo.value.resolved == fuera.resolve()
    assert excinfo.value.project_root == tmp_path.resolve()
    assert excinfo.value.code == "path_outside_project"


def test_a_granted_path_passes(tmp_path: Path) -> None:
    """Una ruta concedida (aprobación puntual) se acepta para esa llamada."""
    ctx = make_ctx(tmp_path, DefaultSecurityPolicy())
    permiso = tmp_path.parent / "concedida"
    permiso.mkdir(exist_ok=True)
    (permiso / "x.txt").write_text("hola", encoding="utf-8")
    ctx.allowed_paths = (permiso,)
    result = run(ReadFileTool().execute({"path": str(permiso / "x.txt")}, ctx))
    assert result.output == "hola"


def test_a_protected_zone_is_not_unlocked_by_a_grant(tmp_path: Path) -> None:
    """Conceder una ruta no puede abrir las zonas internas del framework."""
    import pytest

    from satellite.core.errors import ProtectedZoneForbidden

    ctx = make_ctx(tmp_path, DefaultSecurityPolicy())
    ctx.allowed_paths = (tmp_path,)  # aunque se conceda el proyecto entero
    objetivo = tmp_path / ".satellite" / "state" / "skill_cache.json"
    objetivo.parent.mkdir(parents=True, exist_ok=True)
    objetivo.write_text("{}", encoding="utf-8")
    with pytest.raises(ProtectedZoneForbidden) as excinfo:
        run(ReadFileTool().execute({"path": str(objetivo)}, ctx))
    assert excinfo.value.code == "protected_zone"


def test_the_registry_labels_the_denial(tmp_path: Path) -> None:
    """El resultado trae el código y los detalles, no sólo el texto del error."""
    from satellite.core.tool_models import ToolCall
    from satellite.tools.registry import DefaultToolRegistry

    registry = DefaultToolRegistry(policy=DefaultSecurityPolicy(allowed_tools={"read_file"}))
    registry.register(ReadFileTool())
    ctx = make_ctx(tmp_path, DefaultSecurityPolicy(allowed_tools={"read_file"}))
    resultado = run(
        registry.execute(
            ToolCall(id="c1", name="read_file", arguments={"path": str(tmp_path.parent / "fuera.txt")}),
            ctx,
        )
    )
    assert resultado.success is False
    assert resultado.error_code == "path_outside_project"
    assert resultado.output["path"].endswith("fuera.txt")
