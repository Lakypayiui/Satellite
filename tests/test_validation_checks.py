"""Tests de validadores deterministas."""

import asyncio
from pathlib import Path

from satellite.core.result import AgentResult, AgentStatus
from satellite.validation.checks import FileExistsCheck, JsonSchemaOutputCheck
from satellite.validation.base import ValidationContext


def run(coro):
    return asyncio.run(coro)


def make_result(output: dict) -> AgentResult:
    return AgentResult(node_id="n1", spec_id="s", status=AgentStatus.OK, output=output)


def test_file_exists_ok(tmp_path: Path) -> None:
    (tmp_path / "a.html").write_text("x", encoding="utf-8")
    checker = FileExistsCheck()
    result = run(checker.validate(make_result({"path": "a.html"}), ValidationContext(project_root=tmp_path)))
    assert result.ok is True


def test_file_exists_ko(tmp_path: Path) -> None:
    checker = FileExistsCheck()
    result = run(checker.validate(make_result({"path": "no_existe.html"}), ValidationContext(project_root=tmp_path)))
    assert result.ok is False
    assert "no existe" in result.detail


def test_file_exists_no_path(tmp_path: Path) -> None:
    checker = FileExistsCheck()
    result = run(checker.validate(make_result({}), ValidationContext(project_root=tmp_path)))
    assert result.ok is False


def test_json_schema_ok(tmp_path: Path) -> None:
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    checker = JsonSchemaOutputCheck(schema=schema)
    result = run(checker.validate(make_result({"path": "a"}), ValidationContext(project_root=tmp_path)))
    assert result.ok is True


def test_json_schema_ko(tmp_path: Path) -> None:
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    checker = JsonSchemaOutputCheck(schema=schema)
    result = run(checker.validate(make_result({"otro": 1}), ValidationContext(project_root=tmp_path)))
    assert result.ok is False
