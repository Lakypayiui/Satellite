"""La TUI con el piloto: respuesta en vivo, log limpio y preguntas (plan, fase 4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual.widgets import Input, Static

from satellite.cli import runtime_builder
from satellite.core.events import EventType, ExecutionEvent
from satellite.providers.fake import FakeLLM
from satellite.tui.app import SatelliteTUI
from satellite.tui.session import Session


def make_app(tmp_path: Path, monkeypatch, script: list[dict]) -> SatelliteTUI:
    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    cfg_dir = tmp_path / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m", "orchestration": {"mode": "pilot"}}),
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("print('hola')\n", encoding="utf-8")
    provider = FakeLLM(script=script, default_content="")
    resumidor = FakeLLM(default_content="resumen")
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: resumidor if role == "context" else default,
    )
    return SatelliteTUI(Session(tmp_path, home=home))


def event(kind: EventType, **kwargs) -> ExecutionEvent:
    return ExecutionEvent(event_type=kind, task_id="t1", **kwargs)


def test_live_panel_follows_segments_and_clears(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch, [])
        async with app.run_test() as pilot:
            await pilot.pause()
            live = app.query_one("#live", Static)
            assert live.display is False
            app._on_event(event(EventType.TEXT_DELTA, payload={"text": "Voy a ", "segment": 1}))
            app._on_event(event(EventType.TEXT_DELTA, payload={"text": "leer", "segment": 1}))
            assert app._live_text == "Voy a leer"
            assert live.display is True
            # Otra llamada al modelo: la narración anterior se sustituye.
            app._on_event(event(EventType.TEXT_DELTA, payload={"text": "Respuesta", "segment": 2}))
            assert app._live_text == "Respuesta"
            # Los deltas no ensucian el log.
            assert "text_delta" not in app.transcript
            app._live_clear()
            assert live.display is False

    asyncio.run(scenario())


def test_log_hides_lifecycle_and_compacts_reads(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch, [])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.transcript.clear()
            app._on_event(event(EventType.TASK_STARTED, payload={"goal": "x"}))
            app._on_event(event(EventType.NODE_STARTED, node_id="pilot", payload={"agent_id": "pilot"}))
            app._on_event(
                event(
                    EventType.TOOL_CALLED,
                    node_id="pilot",
                    tool_name="read_file",
                    payload={"arguments": {"path": "app.py"}, "success": True},
                )
            )
            app._on_event(event(EventType.DELEGATION_STARTED, payload={"agent_id": "python_coder"}))
            app._on_event(event(EventType.TASK_FINISHED, payload={"status": "ok"}))
            assert app.transcript == [
                "  · Leyendo app.py",
                "  → python_coder trabajando",
            ]
            # El piloto no aparece como tarjeta de agente; el coder sí.
            assert "pilot" not in app._agents
            assert "python_coder" in app._agents

    asyncio.run(scenario())


def test_a_question_reply_shows_how_to_answer(tmp_path, monkeypatch) -> None:
    script = [
        {
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "ask_user",
                    "arguments": {
                        "question": "¿Qué archivo?",
                        "choices": [{"label": "app.py"}, {"label": "otro"}],
                    },
                }
            ]
        },
        {"content": "Trabajo sobre app.py."},
    ]

    async def scenario():
        app = make_app(tmp_path, monkeypatch, script)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.value = "arréglalo"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "respuesta needs_user_input" in app.transcript
            assert "pregunta abierta" in app.transcript
            prompt.value = "1"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "respuesta ok" in app.transcript
            assert app.query_one("#live", Static).display is False

    asyncio.run(scenario())
