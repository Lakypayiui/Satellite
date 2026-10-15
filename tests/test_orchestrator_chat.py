"""Tests del DefaultOrchestrator en modo chat-directo."""

import asyncio
from pathlib import Path

from satellite.agents.registry import DefaultAgentRegistry
from satellite.core.task import Task
from satellite.orchestration.base import GraphRunner, Planner, RuntimeServices
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


class FakePlanner(Planner):
    def __init__(self):
        self.plans = 0

    async def plan(self, task, catalog, context_hint=()):
        self.plans += 1
        raise AssertionError("NO se debe planificar en modo chat")


class FakeRunner(GraphRunner):
    def __init__(self):
        self.runs = 0

    async def run(self, graph, task, services):
        self.runs += 1
        raise AssertionError("NO se debe ejecutar el grafo en modo chat")


class FailingPlanner(Planner):
    def __init__(self):
        self.plans = 0

    async def plan(self, task, catalog, context_hint=()):
        self.plans += 1
        return None


class Sink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def subscribe(self, handler):
        pass


def make_services(provider) -> RuntimeServices:
    reg = DefaultAgentRegistry()
    return RuntimeServices(registry=reg, provider=provider)


def test_chat_goal_skips_graph(tmp_path: Path) -> None:
    """Pregunta conversacional → responde directo, sin planificar ni correr."""
    # Heurística decide que es chat → solo 1 llamada (la respuesta directa)
    provider = FakeLLM(script=[
        {"content": "La fotosíntesis es un proceso..."},
    ])
    planner = FakePlanner()
    runner = FakeRunner()
    sink = Sink()
    services = make_services(provider)
    services.sink = sink
    orch = DefaultOrchestrator(planner, runner, services, sink=sink)
    task = Task(goal="¿qué es la fotosíntesis?", project_root=tmp_path)
    result = run(orch.execute(task))
    assert result.status.value == "ok"
    assert "fotosíntesis" in result.summary.lower()
    assert result.output.get("mode") == "direct_reply"
    assert planner.plans == 0
    assert runner.runs == 0
    # eventos: task_started + task_finished (sin graph_built ni node_*)
    types = [e.event_type.value for e in sink.events]
    assert types[0] == "task_started"
    assert types[-1] == "task_finished"
    assert "graph_built" not in types


def test_task_goal_does_use_graph(tmp_path: Path) -> None:
    """Tarea de código → va al planner (no corta por chat)."""
    provider = FakeLLM()
    planner = FailingPlanner()
    runner = FakeRunner()
    orch = DefaultOrchestrator(planner, runner, make_services(provider))
    task = Task(goal="crea una página html para el proyecto", project_root=tmp_path)
    try:
        run(orch.execute(task))
    except (AssertionError, TypeError, AttributeError):
        pass
    assert planner.plans >= 1  # intentó planificar: la tarea NO es chat
