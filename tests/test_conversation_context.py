"""La conversación reciente como contexto del agente.

Sin ella, un seguimiento como «explícamela» llegaba sin antecedente: el piloto
contestaba una obviedad porque no sabía a qué se refería.
"""

import asyncio
import json
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.core.context_models import ContextBundle, ContextRefKind
from satellite.core.task import Task
from satellite.orchestration.turn_context import with_conversation
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def _runtime(tmp_path: Path, monkeypatch, provider: FakeLLM):
    """Runtime real con el piloto y un proveedor de mentira."""
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(
        json.dumps({"name": "fake", "model": "fake-model"}), encoding="utf-8"
    )
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(runtime_builder, "_role_provider", lambda _root, _role, default: default)
    orchestrator, _bus, _store, _registry = runtime_builder.build_runtime(project)
    return project, orchestrator


def _historia(*turnos: tuple[str, str]) -> list[dict]:
    return [
        {"role": role, "content": content, "task_id": "t", "status": "ok"}
        for role, content in turnos
    ]


def _enviado(provider: FakeLLM) -> str:
    return "\n".join(message.content for message in provider.requests[-1].messages)


# --- el turno del piloto ---


def test_a_follow_up_reaches_the_model_with_the_previous_turns(
    tmp_path: Path, monkeypatch
) -> None:
    """«Explícamela» tiene que llegar con la lista anterior en el prompt."""
    provider = FakeLLM(script=[{"content": "La carpeta contiene tres archivos: ..."}])
    project, orch = _runtime(tmp_path, monkeypatch, provider)
    task = Task(
        goal="explícamela",
        project_root=project,
        metadata={
            "history": _historia(
                ("user", "lista la carpeta Server"),
                ("assistant", "a.txt, b.txt, notas.md"),
            )
        },
    )

    result = run(orch.execute(task))

    assert result.output["mode"] == "pilot_reply"
    enviado = _enviado(provider)
    assert "Recent conversation" in enviado
    assert "a.txt, b.txt, notas.md" in enviado
    assert "explícamela" in enviado


def test_without_history_there_is_no_conversation_block(tmp_path: Path, monkeypatch) -> None:
    """El primer turno no paga tokens de conversación que no existe."""
    provider = FakeLLM(script=[{"content": "hola"}])
    project, orch = _runtime(tmp_path, monkeypatch, provider)

    run(orch.execute(Task(goal="hola", project_root=project)))

    assert "Recent conversation" not in _enviado(provider)


def test_the_pilot_receives_project_index_context(tmp_path: Path, monkeypatch) -> None:
    provider = FakeLLM(script=[{"content": "Es una aplicación de ejemplo."}])
    project, orch = _runtime(tmp_path, monkeypatch, provider)
    (project / "README.md").write_text("# Demo\naplicación de ejemplo\n", encoding="utf-8")

    run(orch.execute(Task(goal="¿De qué trata este proyecto?", project_root=project)))

    enviado = _enviado(provider)
    assert "project index" in enviado
    assert "aplicación de ejemplo" in enviado


# --- el contexto del turno ---


def test_the_turn_context_carries_the_conversation(tmp_path: Path) -> None:
    """El agente la recibe como sección dinámica, delante del resto del contexto."""
    task = Task(
        goal="haz un resumen de eso",
        project_root=tmp_path,
        metadata={"history": _historia(("assistant", "a.txt, b.txt, notas.md"))},
    )

    bundle = with_conversation(ContextBundle(), task)

    assert [section.ref.kind for section in bundle.sections] == [ContextRefKind.CONVERSATION]
    assert "a.txt, b.txt, notas.md" in bundle.sections[0].content


def test_no_history_means_no_extra_section(tmp_path: Path) -> None:
    task = Task(goal="hola", project_root=tmp_path)
    assert with_conversation(ContextBundle(), task).sections == ()
