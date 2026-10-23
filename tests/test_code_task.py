"""Bounded code/test profiles for the language agents."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace


from satellite.core.tool_models import ToolResult
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools import code_task as code_task_module
from satellite.tools.base import ToolContext
from satellite.tools.code_task import CodeTaskTool


def run(coro):
    return asyncio.run(coro)


def _ctx(tmp_path: Path, agent_id: str = "python_coder", policy=None) -> ToolContext:
    return ToolContext(
        project_root=tmp_path,
        active_spec=SimpleNamespace(id=agent_id),
        policy=policy,
    )


def _fake_argv(monkeypatch, result: ToolResult, calls: list[list[str]]):
    async def fake(argv, **kwargs):
        calls.append(list(argv))
        return result

    monkeypatch.setattr(code_task_module, "execute_argv", fake)
    monkeypatch.setattr(
        code_task_module, "execution_sandbox", lambda _ctx: (None, None)
    )


def test_test_action_uses_pytest_and_reports_failure_as_completed(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    calls: list[list[str]] = []
    _fake_argv(
        monkeypatch,
        ToolResult(
            success=False,
            output={
                "exit_code": 1,
                "stdout": "1 failed, 2 passed in 0.10s",
                "stderr": "",
            },
        ),
        calls,
    )
    result = run(CodeTaskTool().execute({"action": "test"}, _ctx(tmp_path)))
    assert result.success is True
    assert result.output["status"] == "failed"
    assert "-m" in calls[0] and "pytest" in calls[0]
    assert "2 passed" in result.output["summary"]


def test_test_action_applies_filter(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    calls: list[list[str]] = []
    _fake_argv(
        monkeypatch,
        ToolResult(success=True, output={"exit_code": 0, "stdout": "1 passed"}),
        calls,
    )
    result = run(
        CodeTaskTool().execute(
            {"action": "test", "filter": "login"}, _ctx(tmp_path)
        )
    )
    assert result.success is True
    assert "-k" in calls[0] and "login" in calls[0]
    assert result.output["status"] == "passed"


def test_run_action_infers_language_from_agent_and_path(
    tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "hello.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    calls: list[list[str]] = []
    _fake_argv(
        monkeypatch,
        ToolResult(success=True, output={"exit_code": 0, "stdout": "hi\n"}),
        calls,
    )
    result = run(
        CodeTaskTool().execute(
            {"action": "run", "path": "hello.py"}, _ctx(tmp_path)
        )
    )
    assert result.success is True
    assert result.output["language"] == "python"
    assert calls[0][-1] == str(script)


def test_sql_run_is_unsupported(tmp_path: Path) -> None:
    result = run(
        CodeTaskTool().execute(
            {"action": "run", "language": "sql", "path": "schema.sql"},
            _ctx(tmp_path, "sql_coder"),
        )
    )
    assert result.success is False
    assert result.error_code == "code_unsupported"


def test_timeout_is_a_tool_failure(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    _fake_argv(
        monkeypatch,
        ToolResult(success=False, error="timeout de 5s alcanzado"),
        [],
    )
    result = run(CodeTaskTool().execute({"action": "test"}, _ctx(tmp_path)))
    assert result.success is False
    assert result.error_code == "timeout"
    assert result.output["status"] == "timeout"


def test_path_outside_project_is_rejected(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy(allowed_tools={"code_task"})
    result = run(
        CodeTaskTool().execute(
            {"action": "test", "path": "../outside.py"},
            _ctx(tmp_path, policy=policy),
        )
    )
    assert result.success is False
    assert result.error_code == "path_outside_project"


def test_missing_language_is_reported(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path)
    result = run(CodeTaskTool().execute({"action": "test"}, ctx))
    assert result.success is False
    assert result.error_code == "language_required"


def test_python_test_action_runs_only_the_given_path(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    calls: list[list[str]] = []
    _fake_argv(
        monkeypatch,
        ToolResult(success=True, output={"exit_code": 0, "stdout": "1 passed"}),
        calls,
    )
    run(CodeTaskTool().execute({"action": "test", "path": "tests/test_a.py"}, _ctx(tmp_path)))
    run(CodeTaskTool().execute({"action": "test"}, _ctx(tmp_path)))
    assert calls[0][-1] == "tests/test_a.py"
    assert "tests/test_a.py" not in calls[1]


def test_unittest_plan_targets_a_file_or_directory(tmp_path: Path) -> None:
    from satellite.code.profiles import _python_test_plan

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    one = _python_test_plan(tmp_path, "py", None, tmp_path / "tests" / "test_a.py")
    assert one.steps[0].argv[-1] == "tests/test_a.py"
    folder = _python_test_plan(tmp_path, "py", None, tmp_path / "tests")
    assert "discover" in folder.steps[0].argv and "tests" in folder.steps[0].argv
