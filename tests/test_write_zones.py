"""Zonas de escritura: kernel, estado, auditoría y config quedan vetadas."""

import asyncio
import os
from pathlib import Path

import pytest

from satellite.core.tool_models import ToolCall
from satellite.kernel.guard import (
    KernelWriteError,
    assert_write_allowed,
    is_write_allowed,
    package_root,
    protected_zone,
)
from satellite.kernel.paths import resolve_paths
from satellite.security.policy import DefaultSecurityPolicy
from satellite.skills.cache import SkillResultCache
from satellite.tools.base import ToolContext
from satellite.tools.registry import DefaultToolRegistry


def run(coro):
    return asyncio.run(coro)


def make_env(tmp_path: Path, monkeypatch):
    """Proyecto temporal + HOME de satellite aislado."""
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return resolve_paths(project), project


def test_the_workspace_and_the_catalogs_are_writable(tmp_path: Path, monkeypatch) -> None:
    paths, project = make_env(tmp_path, monkeypatch)
    assert is_write_allowed(project / "src" / "main.py", paths, workspace=project) is True
    assert is_write_allowed(paths.agents_root / "nuevo.json", paths, workspace=project) is True
    # fuera del workspace y de los catálogos: no
    assert is_write_allowed(tmp_path / "otro" / "x.py", paths, workspace=project) is False


def test_every_protected_zone_is_refused_by_name(tmp_path: Path, monkeypatch) -> None:
    paths, project = make_env(tmp_path, monkeypatch)
    cases = {
        "kernel": package_root() / "kernel" / "guard.py",
        "state": Path(paths.project_data) / "state" / "skill_cache.json",
        "audit": Path(paths.audit_root) / "audit.jsonl",
        "config": Path(paths.project_data) / "config" / "config.json",
        "global_config": Path(paths.global_root) / "config" / "providers.json",
    }
    for expected, target in cases.items():
        zone = "config" if expected == "global_config" else expected
        assert protected_zone(target, paths) == zone, target
        assert is_write_allowed(target, paths, workspace=project) is False, target
        with pytest.raises(KernelWriteError) as excinfo:
            assert_write_allowed(target, paths, workspace=project)
        assert zone in str(excinfo.value)  # el mensaje nombra la zona


def test_a_symlink_from_the_workspace_into_state_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    paths, project = make_env(tmp_path, monkeypatch)
    state = Path(paths.project_data) / "state"
    state.mkdir(parents=True, exist_ok=True)
    link = project / "atajo"
    try:
        os.symlink(state, link, target_is_directory=True)
    except (OSError, NotImplementedError):  # sin permisos para crear symlinks
        pytest.skip("este sistema no permite crear symlinks sin privilegios")
    assert protected_zone(link / "skill_cache.json", paths) == "state"
    assert is_write_allowed(link / "skill_cache.json", paths, workspace=project) is False


def make_context(project: Path, *tools: str) -> ToolContext:
    policy = DefaultSecurityPolicy(allowed_tools=set(tools))
    return ToolContext(
        project_root=project,
        policy=policy,
        allowed_paths=(project,),
    )


def test_tools_refuse_the_protected_zones(tmp_path: Path, monkeypatch) -> None:
    paths, project = make_env(tmp_path, monkeypatch)
    registry = DefaultToolRegistry(policy=DefaultSecurityPolicy(allowed_tools={"write_file", "read_file"}))
    from satellite.tools.fs import ReadFileTool, WriteFileTool

    registry.register(WriteFileTool())
    registry.register(ReadFileTool())
    ctx = make_context(project, "write_file", "read_file")

    forbidden = run(
        registry.execute(
            ToolCall(
                id="c1",
                name="write_file",
                arguments={"path": ".satellite/state/skill_cache.json", "content": "{}"},
            ),
            ctx,
        )
    )
    assert forbidden.success is False
    assert "zona protegida" in (forbidden.error or "")

    # y el workspace sí
    allowed = run(
        registry.execute(
            ToolCall(id="c2", name="write_file", arguments={"path": "ok.txt", "content": "hola"}),
            ctx,
        )
    )
    assert allowed.success is True
    assert (project / "ok.txt").read_text(encoding="utf-8") == "hola"


def test_the_framework_still_writes_its_own_state(tmp_path: Path, monkeypatch) -> None:
    """El estado y el caché se guardan por su camino, no con una tool."""
    paths, project = make_env(tmp_path, monkeypatch)
    cache_path = Path(paths.project_data) / "state" / "skill_cache.json"
    cache = SkillResultCache(cache_path)
    cache.put("k", {"ok": 1}, skill_id="s1")
    assert cache_path.exists()
    assert SkillResultCache(cache_path).get("k") == {"ok": 1}
    # aunque una tool no podría escribir ahí
    assert is_write_allowed(cache_path, paths, workspace=project) is False
