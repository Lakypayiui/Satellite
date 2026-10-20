"""Overview de proyecto: contexto generado/actualizado y respuesta útil sin modelo.

Regresión del fallo reportado: al preguntar «¿De qué trata el proyecto?» con archivos
indexados, el usuario recibía «No obtuve respuesta del modelo» en vez de mostrar lo que
sí había. Se cubre:

- el contexto se genera automáticamente desde ``project_root`` (sin exploración manual);
- incluye contenido legible de extensiones distintas de la lista clásica (``.vue``,
  ``.lua``…), no sólo el inventario;
- refleja altas, modificaciones y bajas entre turnos (índice reutilizado);
- el overview responde útilmente con lo disponible y pide aclaración si sólo hay
  inventario; nunca dice falsamente que «no hay contexto» si hay archivos relevantes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.agents.registry import DefaultAgentRegistry
from satellite.context.engine import DefaultContextEngine
from satellite.context.project_index import ProjectIndex
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import GraphRunner, Planner, RuntimeServices
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.providers.fake import FakeLLM

OVERVIEW = "¿De qué trata el proyecto?"


def run(coro):
    return asyncio.run(coro)


class _PlannerMustNotBeCalled(Planner):
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, task, catalog, context_hint=()):
        self.calls += 1
        raise AssertionError("un overview no debe planificarse")


class _RunnerMustNotRun(GraphRunner):
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, graph, task, services) -> TaskResult:
        self.runs += 1
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="grafo")


def _orchestrator(project: Path, provider):
    planner = _PlannerMustNotBeCalled()
    runner = _RunnerMustNotRun()
    services = RuntimeServices(
        registry=DefaultAgentRegistry(),
        provider=provider,
        context=DefaultContextEngine(project),
    )
    return DefaultOrchestrator(planner, runner, services, allow_chat_direct=True), planner, runner


# --------------------------------------------------- generación y contenido


def test_context_includes_content_for_non_classic_extension(tmp_path: Path) -> None:
    """Un archivo relevante con extensión fuera de la lista clásica también aporta texto.

    ``main.lua`` puntúa como punto de entrada (nombre ``main.*``), así que se selecciona
    y su contenido debe entrar al contexto (antes se descartaba por extensión).
    """
    (tmp_path / "main.lua").write_text('print("hola lua")\n', encoding="utf-8")

    context = ProjectIndex(tmp_path).context_for_goal(OVERVIEW)

    assert "main.lua" in context
    assert "hola lua" in context


def test_named_file_is_read_and_virtualenvs_are_ignored(tmp_path: Path) -> None:
    """Un archivo nombrado se prioriza; un virtualenv no contamina el contexto."""
    (tmp_path / "test.py").write_text(
        "# test.py\n\nprint('contenido unico')\n", encoding="utf-8"
    )
    venv = tmp_path / "custom_env"
    (venv / "Lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (venv / "Lib" / "__main__.py").write_text("print('venv interno')\n", encoding="utf-8")

    context = ProjectIndex(tmp_path).context_for_goal("¿qué hace test.py?")

    assert "test.py ---" in context
    assert "contenido unico" in context
    assert "custom_env" not in context
    assert "venv interno" not in context


def test_context_inventory_lists_files_even_without_readable_content(tmp_path: Path) -> None:
    """El inventario (árbol) lista los archivos aunque no se pueda leer su contenido."""
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02binario")

    context = ProjectIndex(tmp_path).context_for_goal(OVERVIEW)

    assert "data.bin" in context


# --------------------------------------------------- altas / modificaciones / bajas


def test_index_context_reflects_added_file_next_turn(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text("def base():\n    pass\n", encoding="utf-8")
    index = ProjectIndex(tmp_path)
    index.ensure_persisted()
    assert "nuevo.py" not in index.context_for_goal(OVERVIEW)

    (tmp_path / "nuevo.py").write_text("def nuevo():\n    pass\n", encoding="utf-8")
    index.ensure_persisted()

    assert "nuevo.py" in index.context_for_goal(OVERVIEW)


def test_index_context_reflects_modified_file_next_turn(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def v1():\n    pass\n", encoding="utf-8")
    index = ProjectIndex(tmp_path)
    index.ensure_persisted()
    assert "v1" in index.context_for_goal("¿qué hace app.py?")

    (tmp_path / "app.py").write_text("def v2():\n    pass\n", encoding="utf-8")
    index.ensure_persisted()
    context = index.context_for_goal("¿qué hace app.py?")

    assert "v2" in context
    assert "v1" not in context


def test_index_context_drops_deleted_file_next_turn(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "extra.py").write_text("def extra():\n    pass\n", encoding="utf-8")
    index = ProjectIndex(tmp_path)
    index.ensure_persisted()
    assert "extra.py" in index.context_for_goal(OVERVIEW)

    (tmp_path / "extra.py").unlink()
    index.ensure_persisted()

    assert "extra.py" not in index.context_for_goal(OVERVIEW)


# --------------------------------------------------- respuesta del overview


def test_overview_model_empty_uses_available_context(tmp_path: Path) -> None:
    """Con contexto indexado, una completion vacía no se convierte en «no obtuve respuesta»."""
    (tmp_path / "README.md").write_text(
        "Centinela administra satélites de prueba.", encoding="utf-8"
    )
    provider = FakeLLM(script=[{"content": ""}])
    orch, planner, runner = _orchestrator(tmp_path, provider)

    reply = run(ChatSession(tmp_path, orch, persist=False).send(OVERVIEW))

    assert planner.calls == 0
    assert runner.runs == 0
    assert "No obtuve respuesta del modelo" not in reply.text
    assert "Centinela administra satélites de prueba" in reply.text


def test_overview_inventory_only_asks_for_clarification(tmp_path: Path) -> None:
    """Si sólo se pudo inventariar (nada legible), se muestra el inventario y se pregunta."""
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02binario")
    provider = FakeLLM(script=[{"content": ""}])
    orch, _planner, _runner = _orchestrator(tmp_path, provider)

    reply = run(ChatSession(tmp_path, orch, persist=False).send(OVERVIEW))

    assert "data.bin" in reply.text
    assert "no pude leer contenido" in reply.text
    assert "No obtuve respuesta del modelo" not in reply.text


def test_inventory_present_is_not_reported_as_no_context(tmp_path: Path) -> None:
    """Con archivos relevantes no se dice «no hay contexto»: el inventario cuenta."""
    (tmp_path / "app.vue").write_text("<template>hola</template>\n", encoding="utf-8")
    provider = FakeLLM(script=[{"content": ""}])
    orch, _planner, _runner = _orchestrator(tmp_path, provider)

    task = Task(goal=OVERVIEW, project_root=tmp_path)
    run(orch.execute(task))

    assert task.metadata["project_context_status"] == "ok"
    assert "app.vue" in task.metadata["project_context"]

    reply = run(ChatSession(tmp_path, orch, persist=False).send(OVERVIEW))
    assert "No hay contexto de proyecto" not in reply.text
