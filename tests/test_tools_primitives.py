"""Tests de los primitivos base y de las tools de skills (Fase 3)."""

import asyncio
from pathlib import Path

from satellite.core.tool_models import ToolResult
from satellite.kernel.manifest import RESERVED_SKILL_IDS
from satellite.skills.models import SkillManifest
from satellite.skills.registry import SkillRegistry
from satellite.tools.base import ToolContext
from satellite.tools.primitives import RunPythonTool, RunArgvTool, SearchFilesTool
from satellite.tools.skills import ProposeSkillTool, SearchSkillsTool


def run(coro):
    return asyncio.run(coro)


def test_run_python_tool(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path)
    result = run(RunPythonTool().execute({"code": "print(2 + 3)"}, ctx))
    assert result.success is True
    assert "5" in result.output["stdout"]


def test_run_argv_tool_id_is_reserved(tmp_path: Path) -> None:
    assert RunArgvTool().id == "run_argv"
    assert RunArgvTool().id in RESERVED_SKILL_IDS


def test_search_files_tool(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y", encoding="utf-8")
    ctx = ToolContext(project_root=tmp_path)
    result = run(SearchFilesTool().execute({"pattern": "**/*.py"}, ctx))
    assert result.success is True
    paths = {entry["path"] for entry in result.output}
    assert paths == {"a.py", "sub/b.py"}


def test_search_skills_without_registry(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path)
    result = run(SearchSkillsTool().execute({"query": "x"}, ctx))
    assert result.success is False
    assert "no disponible" in (result.error or "")


def test_search_skills_with_registry(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.register(
        SkillManifest(id="count_words", description="Cuenta las palabras de un texto"), "print(1)"
    )
    ctx = ToolContext(project_root=tmp_path, skills=registry)
    result = run(SearchSkillsTool().execute({"query": "contar palabras"}, ctx))
    assert result.success is True
    assert result.output[0]["id"] == "count_words"


def test_propose_skill_requires_evidence(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path)
    args = {"id": "new_skill", "description": "d", "script": "print(1)", "rationale": "r"}
    result = run(ProposeSkillTool().execute(args, ctx))
    assert result.success is False
    assert "ejecución exitosa" in (result.error or "")


def test_propose_skill_records_pending(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path, task_state={"successful_runs": ["x"]})
    args = {"id": "new_skill", "description": "d", "script": "print(1)", "rationale": "r"}
    result = run(ProposeSkillTool().execute(args, ctx))
    assert result.success is True
    assert ctx.task_state["pending_skills"][0]["id"] == "new_skill"


def test_propose_skill_invalid_payload(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path, task_state={"successful_runs": ["x"]})
    args = {"id": "Bad Id", "description": "d", "script": "s", "rationale": "r"}
    result = run(ProposeSkillTool().execute(args, ctx))
    assert result.success is False
