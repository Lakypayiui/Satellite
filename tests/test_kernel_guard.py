"""Tests de resolución de rutas de datos y del guard de escritura (Fase 1)."""

from pathlib import Path

import pytest

from satellite.core.errors import ToolError
from satellite.kernel.guard import (
    KernelWriteError,
    assert_write_allowed,
    is_write_allowed,
    package_root,
)
from satellite.kernel.paths import ensure_dirs, resolve_paths


def test_resolve_paths_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SATELLITE_HOME", raising=False)
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    monkeypatch.delenv("SATELLITE_PROJECT_ROOT", raising=False)
    paths = resolve_paths(project_root=tmp_path / "proj")
    assert paths.agents_root == paths.global_root / "agents"
    assert paths.skills_root == paths.global_root / "skills"
    assert paths.audit_root == paths.global_root / "audit"
    assert paths.project_data == (tmp_path / "proj").resolve() / ".satellite"


def test_resolve_paths_env_overrides(tmp_path: Path) -> None:
    env = {
        "SATELLITE_HOME": str(tmp_path / "home"),
        "SATELLITE_SKILLS_DIR": str(tmp_path / "global-skills"),
        "SATELLITE_PROJECT_ROOT": str(tmp_path / "proj"),
    }
    paths = resolve_paths(env=env)
    assert paths.global_root == tmp_path / "home"
    assert paths.skills_root == tmp_path / "global-skills"
    assert paths.agents_root == tmp_path / "home" / "agents"
    assert paths.project_data == (tmp_path / "proj").resolve() / ".satellite"


def test_ensure_dirs_creates_all(tmp_path: Path) -> None:
    env = {"SATELLITE_HOME": str(tmp_path / "home")}
    paths = ensure_dirs(resolve_paths(project_root=tmp_path / "proj", env=env))
    assert paths.agents_root.is_dir()
    assert paths.skills_root.is_dir()
    assert paths.audit_root.is_dir()
    assert (paths.project_data / "metrics").is_dir()
    assert (paths.project_data / "snapshots").is_dir()


def test_guard_allows_data_roots(tmp_path: Path) -> None:
    paths = ensure_dirs(resolve_paths(project_root=tmp_path / "proj", env={"SATELLITE_HOME": str(tmp_path / "home")}))
    assert is_write_allowed(paths.agents_root / "new.json", paths)
    assert is_write_allowed(paths.skills_root / "s" / "manifest.json", paths)
    assert is_write_allowed(paths.project_data / "state" / "x.json", paths)


def test_guard_denies_audit_and_kernel(tmp_path: Path) -> None:
    paths = ensure_dirs(resolve_paths(project_root=tmp_path / "proj", env={"SATELLITE_HOME": str(tmp_path / "home")}))
    assert not is_write_allowed(paths.audit_root / "audit.jsonl", paths)
    assert not is_write_allowed(package_root() / "core" / "events.py", paths)
    with pytest.raises(KernelWriteError):
        assert_write_allowed(package_root() / "core" / "events.py", paths)


def test_guard_denies_paths_outside_data(tmp_path: Path) -> None:
    paths = resolve_paths(project_root=tmp_path / "proj", env={"SATELLITE_HOME": str(tmp_path / "home")})
    assert not is_write_allowed(tmp_path / "somewhere-else.txt", paths)
    with pytest.raises(ToolError):
        assert_write_allowed(tmp_path / "somewhere-else.txt", paths)


def test_guard_traversal_neutralized(tmp_path: Path) -> None:
    paths = resolve_paths(project_root=tmp_path / "proj", env={"SATELLITE_HOME": str(tmp_path / "home")})
    escape = paths.agents_root / ".." / ".." / "escape.json"
    assert not is_write_allowed(escape, paths)
