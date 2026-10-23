"""Un worktree registrado fuera de la raíz del proyecto es un directorio de trabajo
completo: se escribe en él, se busca en él y sus cambios se ven y se deshacen.

Antes ``active_workdir`` lo aceptaba, pero las tools lo validaban contra la raíz original
(``path_outside_project``) y el registro de cambios lo descartaba en silencio.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from satellite.persistence.changes import ChangeLedger
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import ToolContext
from satellite.tools.fs import WriteFileTool
from satellite.tools.primitives import SearchFilesTool


def run(coro):
    return asyncio.run(coro)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def sibling_worktree(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "t")
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(project, "add", "app.py")
    _git(project, "commit", "-q", "-m", "init")
    worktree = tmp_path / "sibling"
    _git(project, "worktree", "add", "-q", str(worktree), "-b", "feat")
    return project.resolve(), worktree.resolve()


def _ctx(project: Path, worktree: Path) -> ToolContext:
    policy = DefaultSecurityPolicy(allowed_tools={"write_file", "search_files"})
    return ToolContext(project_root=project, workdir=worktree, policy=policy, task_id="t1")


def test_the_sibling_worktree_is_accepted_as_workdir(sibling_worktree) -> None:
    from satellite import gitops

    project, worktree = sibling_worktree
    gitops.set_active_workdir(project, str(worktree))

    assert gitops.active_workdir(project) == worktree


def test_a_write_in_the_worktree_is_allowed_recorded_shown_and_undone(sibling_worktree) -> None:
    project, worktree = sibling_worktree
    ctx = _ctx(project, worktree)

    result = run(WriteFileTool().execute({"path": "app.py", "content": "x = 2\n"}, ctx))

    assert result.success is True, result.error
    assert (worktree / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    ledger = ChangeLedger(project)
    assert ledger.files("t1") == ["app.py"]
    diff = ledger.diff("t1")
    assert "-x = 1" in diff and "+x = 2" in diff

    restored = ledger.restore("t1")

    assert restored["restored"] == ["app.py"]
    assert (worktree / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 1\n"


def test_search_files_works_in_the_worktree(sibling_worktree) -> None:
    project, worktree = sibling_worktree
    (worktree / "app.py").write_text("x = 1\nneedle = 2\n", encoding="utf-8")

    found = run(SearchFilesTool().execute({"pattern": "*.py", "content": "needle"}, _ctx(project, worktree)))

    assert found.success is True, found.error
    assert [(m["path"], m["line"]) for m in found.output["matches"]] == [("app.py", 2)]


def test_paths_outside_both_roots_are_still_refused(sibling_worktree, tmp_path) -> None:
    project, worktree = sibling_worktree
    outside = tmp_path / "otro"
    outside.mkdir()

    from satellite.core.errors import PathOutsideProject

    with pytest.raises(PathOutsideProject):
        run(
            WriteFileTool().execute(
                {"path": str(outside / "x.txt"), "content": "no"}, _ctx(project, worktree)
            )
        )
    assert not (outside / "x.txt").exists()


def test_restore_ignores_a_base_that_is_no_longer_a_worktree(sibling_worktree, tmp_path) -> None:
    """El manifiesto es un archivo: una base que ya no es worktree registrado no se toca."""
    project, worktree = sibling_worktree
    ctx = _ctx(project, worktree)
    run(WriteFileTool().execute({"path": "app.py", "content": "x = 2\n"}, ctx))
    _git(project, "worktree", "remove", "--force", str(worktree))
    worktree.mkdir()
    (worktree / "app.py").write_text("ajeno\n", encoding="utf-8")

    restored = ChangeLedger(project).restore("t1")

    assert restored["restored"] == []
    assert (worktree / "app.py").read_text(encoding="utf-8") == "ajeno\n"
