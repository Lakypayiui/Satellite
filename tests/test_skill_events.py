"""Eventos reales de skill: usada no es lo mismo que disponible.

El panel derecho se alimenta de esto, así que lo que se fija aquí es que una skill
**nunca** aparece como ejecutada por estar en el catálogo.
"""

from pathlib import Path

from satellite.core.events import EventType
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


class Bus:
    """Sink de eventos que guarda lo emitido."""

    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        return


def run(coro):
    import asyncio

    return asyncio.run(coro)


def setup(tmp_path: Path, script: str = ECHO) -> tuple[Bus, SkillRegistry, SkillRunner]:
    bus = Bus()
    registry = SkillRegistry(tmp_path / "skills", sink=bus)
    registry.register(
        SkillManifest(id="echo_skill", description="Hace eco", entrypoint="main.py"),
        script,
    )
    return bus, registry, SkillRunner(registry, SubprocessSandbox())


def test_a_skill_that_is_only_available_emits_nothing(tmp_path: Path) -> None:
    """La regla del enunciado: disponible en el catálogo no es usada."""
    bus, _registry, _runner = setup(tmp_path)

    assert bus.events == []


def test_running_a_skill_emits_skill_executed(tmp_path: Path) -> None:
    bus, _registry, runner = setup(tmp_path)

    result = run(runner.run("echo_skill", {"x": 3}, project_root=tmp_path, task_id="t1"))

    assert result.success is True
    emitidos = [e for e in bus.events if e.event_type is EventType.SKILL_EXECUTED]
    assert len(emitidos) == 1
    assert emitidos[0].payload["skill_id"] == "echo_skill"
    assert emitidos[0].payload["cached"] is False
    assert emitidos[0].task_id == "t1"


def test_a_failing_skill_emits_skill_failed_with_its_reason(tmp_path: Path) -> None:
    bus, _registry, runner = setup(tmp_path, FAIL)

    result = run(runner.run("echo_skill", {}, project_root=tmp_path, task_id="t2"))

    assert result.success is False
    fallos = [e for e in bus.events if e.event_type is EventType.SKILL_FAILED]
    assert len(fallos) == 1
    assert fallos[0].payload["skill_id"] == "echo_skill"
    assert fallos[0].payload["exit_code"] == 2
    assert fallos[0].task_id == "t2"
    assert not [e for e in bus.events if e.event_type is EventType.SKILL_EXECUTED]


def test_without_a_task_the_event_says_who_ran_and_stays_attributable(tmp_path: Path) -> None:
    bus, _registry, runner = setup(tmp_path)

    run(runner.run("echo_skill", {}, project_root=tmp_path))

    assert bus.events[0].task_id == "system"
    assert bus.events[0].payload["skill_id"] == "echo_skill"


def test_a_runner_without_a_sink_does_not_explode(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.register(
        SkillManifest(id="echo_skill", description="d", entrypoint="main.py"), ECHO
    )
    runner = SkillRunner(registry, SubprocessSandbox())

    result = run(runner.run("echo_skill", {"x": 1}, project_root=tmp_path))

    assert result.success is True
