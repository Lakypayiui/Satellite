"""Tests del RequestContextTool."""

import asyncio
from pathlib import Path

from satellite.context.engine import DefaultContextEngine
from satellite.approval.base import AutoApprovalPort, DenyAllApprovalPort
from satellite.tools.base import ToolContext
from satellite.tools.context_tool import RequestContextTool


def run(coro):
    return asyncio.run(coro)


def test_with_engine_returns_content(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hola mundo", encoding="utf-8")
    engine = DefaultContextEngine(tmp_path)
    ctx = ToolContext(project_root=tmp_path)
    ctx.engine = engine  # inyección del factory en Etapa 4
    result = run(
        RequestContextTool().execute(
            {"refs": [{"kind": "path", "value": "a.txt"}]}, ctx
        )
    )
    assert result.success is True
    assert result.output["sections"][0]["content"] == "hola mundo"


def test_without_engine_returns_error(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path)
    result = run(
        RequestContextTool().execute(
            {"refs": [{"kind": "path", "value": "a.txt"}]}, ctx
        )
    )
    assert result.success is False
    assert "context engine no disponible" in (result.error or "")


def test_invalid_refs_error(tmp_path: Path) -> None:
    engine = DefaultContextEngine(tmp_path)
    ctx = ToolContext(project_root=tmp_path)
    ctx.engine = engine
    result = run(
        RequestContextTool().execute(
            {"refs": [{"kind": "desconocido", "value": "x"}]}, ctx
        )
    )
    assert result.success is False


def test_absolute_path_outside_project_requires_approval(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    ctx = ToolContext(project_root=project, approval=DenyAllApprovalPort())
    ctx.engine = DefaultContextEngine(project)

    result = run(RequestContextTool().execute(
        {"refs": [{"kind": "path", "value": str(outside)}]}, ctx
    ))

    assert result.success is False
    assert "denegada" in (result.error or "")


def test_approved_outside_context_is_read_once(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    ctx = ToolContext(project_root=project, approval=AutoApprovalPort())
    ctx.engine = DefaultContextEngine(project)

    result = run(RequestContextTool().execute(
        {"refs": [{"kind": "path", "value": str(outside)}]}, ctx
    ))

    assert result.success is True
    assert result.output["sections"][0]["content"] == "private"
