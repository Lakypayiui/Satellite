"""Fase D del plan de correcciones (docs/PLAN-correcciones.md), punto 12.

Cuando ``run_command`` falla, el error dice qué tool usar: ``code_task`` para los tests,
``read_file``/``list_dir``/``search_files`` para mirar archivos. En la evaluación v6 un
coder probó cuatro variantes de ``pytest`` seguidas tras la primera denegación.
"""

from __future__ import annotations

import pytest

from satellite.agents.runtime import command_hint


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q tests/test_x.py",
        "python -m pytest tests/test_x.py",
        "/usr/bin/python3 -m unittest tests.test_x",
        "npm test",
        "go test ./...",
        "cargo test",
    ],
)
def test_a_test_runner_points_to_code_task(command: str) -> None:
    hint = command_hint(command)
    assert hint is not None and "code_task" in hint and "test" in hint


@pytest.mark.parametrize(
    "command",
    [
        "wc -l -c docs/a.mmd",
        "cat satellite/x.py",
        "head -50 README.md",
        "ls -la satellite",
        "grep -rn foo satellite",
        "rg foo",
        "find . -name '*.py'",
        'python3 -c "import pathlib; print(pathlib.Path(\'a\').read_text())"',
    ],
)
def test_file_inspection_points_to_the_file_tools(command: str) -> None:
    hint = command_hint(command)
    assert hint is not None
    assert "read_file" in hint or "search_files" in hint or "list_dir" in hint


def test_other_commands_get_no_hint() -> None:
    assert command_hint("make build") is None
    assert command_hint("") is None


def test_the_hint_reaches_the_model_when_the_command_fails(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    from satellite.approval.base import DenyAllApprovalPort
    from satellite.cli import runtime_builder
    from satellite.orchestration.chat import ChatSession
    from satellite.providers.fake import FakeLLM

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"name": "fake", "model": "m"}), encoding="utf-8")
    coder_reply = {"summary": "s", "files_changed": [], "verification": {"passed": False}}
    provider = FakeLLM(
        script=[
            {"tool_calls": [{"id": "d", "name": "delegate", "arguments": {
                "agent_id": "python_coder", "task": {"goal": "run the tests"}}}]},
            {"tool_calls": [{"id": "r", "name": "run_command", "arguments": {
                "command": "pytest -q tests/test_x.py"}}]},
            {"content": json.dumps(coder_reply)},
            {"content": "ok"},
        ],
        default_content="",
    )
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: FakeLLM(default_content="r") if role == "context" else default,
    )
    orchestrator, _bus, _store, _registry = runtime_builder.build_runtime(project)

    asyncio.run(
        ChatSession(project, orchestrator).send("corre los tests", approval=DenyAllApprovalPort())
    )

    tool_message = json.loads(provider.requests[2].messages[-1].content)
    assert tool_message["success"] is False
    assert "code_task" in (tool_message["error"] or "")
