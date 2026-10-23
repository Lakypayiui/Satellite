"""Contrato observable de un turno de conversación.

Estos tests miran sólo lo que ve una superficie (TUI, escritorio, CLI): el texto de la
respuesta, el estado, el historial y los eventos del bus. No miran cómo decide el
orquestador por dentro (docs/PLAN-flujo-piloto.md, fase 0).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.core.events import EventType
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.pilot import PilotOrchestrator
from satellite.providers.fake import FakeLLM

def run(coro):
    return asyncio.run(coro)


def _write_config(project: Path, extra: dict | None = None) -> None:
    cfg_dir = project / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "fake-model", **(extra or {})}),
        encoding="utf-8",
    )


def _runtime(tmp_path: Path, monkeypatch, script: list[dict], extra: dict | None = None):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    _write_config(project, extra)
    provider = FakeLLM(script=script, default_content="")
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    # Los roles que trabajan el turno usan el proveedor guionizado; ``context`` (resúmenes
    # de turno y compactación) tiene el suyo, para contar sólo las llamadas del turno.
    resumidor = FakeLLM(default_content="resumen")
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: resumidor if role == "context" else default,
    )
    orchestrator, bus, _store, _registry = runtime_builder.build_runtime(project)
    events: list = []
    bus.subscribe(events.append)
    return project, orchestrator, events, provider


def test_an_old_legacy_flag_still_gets_the_pilot(tmp_path: Path, monkeypatch) -> None:
    # Una config de una versión anterior con ``orchestration.mode: legacy`` sigue cargando.
    _project, orchestrator, _events, _provider = _runtime(
        tmp_path, monkeypatch, [], {"orchestration": {"mode": "legacy"}, "direct_reply": True}
    )
    assert isinstance(orchestrator, PilotOrchestrator)
    assert orchestrator.mode == "pilot"
    # Las superficies leen estos atributos directamente.
    assert orchestrator.services is not None
    assert callable(orchestrator.cancel)
    assert orchestrator.cancel("", "test")["cancelled"] is True


def test_greeting_turn_contract(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, events, provider = _runtime(
        tmp_path, monkeypatch, [{"content": "¡Hola! ¿En qué te ayudo?"}]
    )
    chat = ChatSession(project, orchestrator)

    reply = run(chat.send("hola"))

    assert reply.status == "ok"
    assert reply.text == "¡Hola! ¿En qué te ayudo?"
    assert [turn.role for turn in chat.history] == ["user", "assistant"]
    assert chat.history[-1].content == reply.text
    assert reply.task_id
    # Un saludo no gasta más de una llamada al modelo.
    assert len(provider.requests) == 1
    kinds = [event.event_type for event in events]
    assert kinds[0] is EventType.TASK_STARTED
    assert kinds[-1] is EventType.TASK_FINISHED
    assert {event.task_id for event in events} == {reply.task_id}
    # El texto llega en streaming mientras se escribe, y coincide con la respuesta.
    deltas = [e.payload["text"] for e in events if e.event_type is EventType.TEXT_DELTA]
    assert deltas
    assert "".join(deltas) == reply.text


def test_provider_failure_is_a_failed_turn_not_a_crash(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, _events, _provider = _runtime(
        tmp_path, monkeypatch, [{"error": "upstream 500"}]
    )
    chat = ChatSession(project, orchestrator)

    reply = run(chat.send("hola"))

    assert reply.status == "failed"
    assert "upstream 500" in reply.text
    assert chat.history[-1].role == "assistant"
