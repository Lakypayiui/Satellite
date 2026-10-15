"""Tests del SkillRunner: subproceso con JSON stdin/stdout (Fase 3)."""

from pathlib import Path

import pytest

from satellite.core.errors import ToolError
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox
from satellite.skills.models import SkillManifest
from satellite.skills.registry import SkillRegistry
from satellite.skills.runner import SkillRunner

ECHO = (
    "import json, sys\n"
    "data = json.load(sys.stdin)\n"
    "print(json.dumps({'echo': data.get('x')}))\n"
)
FAIL = "import sys\nsys.exit(2)\n"
NOT_JSON = "print('no soy json')\n"


def setup_registry(tmp_path: Path, script: str, *, outputs: dict | None = None) -> tuple[SkillRegistry, SkillRunner]:
    registry = SkillRegistry(tmp_path / "skills")
    registry.register(
        SkillManifest(
            id="echo_skill",
            description="Hace eco de la entrada",
            entrypoint="main.py",
            outputs=outputs or {},
        ),
        script,
    )
    runner = SkillRunner(registry, SubprocessSandbox())
    return registry, runner


def run(coro):
    import asyncio

    return asyncio.run(coro)


def test_run_success(tmp_path: Path) -> None:
    registry, runner = setup_registry(tmp_path, ECHO)
    result = run(runner.run("echo_skill", {"x": 7}, project_root=tmp_path))
    assert result.success is True
    assert result.output == {"echo": 7}
    assert registry.get("echo_skill").stats.successes == 1


def test_run_nonzero_exit(tmp_path: Path) -> None:
    registry, runner = setup_registry(tmp_path, FAIL)
    result = run(runner.run("echo_skill", {}, project_root=tmp_path))
    assert result.success is False
    assert result.exit_code == 2
    assert registry.get("echo_skill").stats.failures == 1


def test_run_invalid_json(tmp_path: Path) -> None:
    registry, runner = setup_registry(tmp_path, NOT_JSON)
    result = run(runner.run("echo_skill", {}, project_root=tmp_path))
    assert result.success is False
    assert "JSON" in (result.error or "")


def test_run_output_schema_mismatch(tmp_path: Path) -> None:
    registry, runner = setup_registry(
        tmp_path, ECHO, outputs={"type": "object", "required": ["missing_key"]}
    )
    result = run(runner.run("echo_skill", {"x": 1}, project_root=tmp_path))
    assert result.success is False
    assert "outputs" in (result.error or "")


def test_build_argv_languages(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    registry.register(SkillManifest(id="py_skill", description="d", entrypoint="main.py"), "print(1)")
    registry.register(
        SkillManifest(id="sh_skill", description="d", language="bash", entrypoint="run.sh"), "echo hi"
    )
    runner = SkillRunner(registry, SubprocessSandbox())
    assert runner.build_argv(registry.get("py_skill"))[1].endswith("main.py")
    assert runner.build_argv(registry.get("sh_skill"))[0] == "bash"


def test_build_argv_unsupported_language(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    registry.register(SkillManifest(id="rb_skill", description="d", language="ruby"), "puts 1")
    runner = SkillRunner(registry, SubprocessSandbox())
    with pytest.raises(ToolError):
        runner.build_argv(registry.get("rb_skill"))


def test_run_unknown_skill(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    runner = SkillRunner(registry, SubprocessSandbox())
    with pytest.raises(ToolError):
        run(runner.run("nope", {}, project_root=tmp_path))
