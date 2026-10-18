"""Git y worktrees: estado real, stage/commit y contexto de ejecución.

Estos tests requieren ``git`` en el PATH; si no está, se saltan (no se simula).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from satellite import gitops

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git no está instalado")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("hola\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "inicial")
    return root


def test_status_reports_branch_and_groups(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    limpio = gitops.status(root)
    assert limpio["is_repo"] is True
    assert limpio["branch"]
    assert limpio["clean"] is True

    (root / "a.txt").write_text("cambiado\n", encoding="utf-8")
    (root / "nuevo.txt").write_text("nuevo\n", encoding="utf-8")
    sucio = gitops.status(root)

    assert sucio["clean"] is False
    assert any(f["path"] == "a.txt" and f["unstaged"] for f in sucio["unstaged"])
    assert any(f["path"] == "nuevo.txt" for f in sucio["untracked"])


def test_stage_and_commit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "a.txt").write_text("v2\n", encoding="utf-8")

    assert gitops.stage(root, ["a.txt"])["ok"] is True
    preparado = gitops.status(root)
    assert any(f["path"] == "a.txt" for f in preparado["staged"])

    resultado = gitops.commit(root, "cambio")
    assert resultado["ok"] is True
    assert gitops.status(root)["clean"] is True


def test_paths_cannot_escape_the_repo(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    with pytest.raises(gitops.GitError):
        gitops.stage(root, ["../fuera.txt"])


def test_a_non_repo_is_a_useful_error(tmp_path: Path) -> None:
    fuera = tmp_path / "sin-repo"
    fuera.mkdir()

    assert gitops.is_repo(fuera) is False
    with pytest.raises(gitops.GitError):
        gitops.status(fuera)


def test_worktree_create_activate_and_remove(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    creado = gitops.add_worktree(root, "feature")
    destino = Path(creado["path"]).resolve()

    assert destino.is_dir()
    assert any(wt["branch"] == "wt/feature" for wt in gitops.list_worktrees(root))
    assert gitops.active_workdir(root) is None

    assert gitops.set_active_workdir(root, creado["path"]) == destino
    assert gitops.active_workdir(root) == destino

    # El runtime lee ese mismo workdir al armarse (no se queda en el proyecto).
    from satellite.cli.runtime_builder import _active_workdir

    assert _active_workdir(root) == destino

    assert gitops.set_active_workdir(root, None) is None
    assert gitops.active_workdir(root) is None

    gitops.remove_worktree(root, creado["path"])
    assert not destino.exists()


def test_removing_a_dirty_worktree_needs_force(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    creado = gitops.add_worktree(root, "dirty")
    (Path(creado["path"]) / "nuevo.txt").write_text("x", encoding="utf-8")

    with pytest.raises(gitops.GitError):
        gitops.remove_worktree(root, creado["path"])

    gitops.remove_worktree(root, creado["path"], force=True)
    assert not Path(creado["path"]).exists()
