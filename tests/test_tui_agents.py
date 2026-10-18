"""Panel de agentes de la TUI: los rectángulos de la derecha."""

import asyncio
import json
from pathlib import Path

from satellite.core.events import EventType as E
from satellite.core.events import ExecutionEvent
from satellite.tui.app import SatelliteTUI
from satellite.tui.render import CARD_WIDTH, agent_card
from satellite.tui.session import Session


def make_app(tmp_path: Path, monkeypatch) -> SatelliteTUI:
    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    cfg_dir = tmp_path / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
    )
    return SatelliteTUI(Session(tmp_path, home=home))


def event(kind: E, *, node_id: str = "", payload: dict | None = None, tool: str = ""):
    return ExecutionEvent(
        event_type=kind, task_id="t1", node_id=node_id, tool_name=tool, payload=payload or {}
    )


def card_text(app: SatelliteTUI, agent: str) -> str:
    """Lo que muestra la tarjeta, reconstruido con la misma función de render."""
    state = app._agents[agent]
    return str(agent_card(agent, state["status"], state["action"]))


# --- render de la tarjeta ---


def test_card_shows_who_it_is_and_what_it_does() -> None:
    text = str(agent_card("skill_curator", "running", "Ejecutando tests"))
    assert "skill_curator" in text
    assert "en curso" in text
    assert "Ejecutando tests" in text


def test_card_clips_long_content_and_marks_the_state() -> None:
    text = str(agent_card("x" * 40, "failed", "y" * 80, detail="z" * 80))
    assert "✗ fallo" in text
    assert text.count("…") >= 3
    for line in text.splitlines():
        assert len(line) <= CARD_WIDTH


def test_card_without_action_says_so() -> None:
    assert "sin actividad" in str(agent_card("orchestrator", "idle"))


# --- comportamiento del panel ---


def test_panel_appears_only_when_an_agent_works(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#agents-panel")
            assert panel.display is False  # en reposo no ocupa sitio
            assert len(app.query(".agent-card")) == 0

            app._on_event(event(E.TASK_STARTED))
            app._on_event(event(E.NODE_STARTED, node_id="n1", payload={"spec": "skill_curator"}))
            await pilot.pause()
            assert panel.display is True
            assert len(app.query(".agent-card")) == 1

    asyncio.run(scenario())


def test_one_rectangle_per_agent_with_its_current_action(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_event(event(E.NODE_STARTED, node_id="n1", payload={"spec": "curador"}))
            app._on_event(event(E.NODE_STARTED, node_id="n2", payload={"spec": "orchestrator"}))
            app._on_event(
                event(
                    E.TOOL_CALLED,
                    node_id="n1",
                    tool="read_file",
                    payload={"arguments": {"path": "docs/README.md"}},
                )
            )
            await pilot.pause()

            assert len(app.query(".agent-card")) == 2
            # acción semántica (sin engranes): nombra lo que se está haciendo
            assert "Leyendo README.md" in card_text(app, "curador")
            assert "razonando" in card_text(app, "orchestrator")
            # el que trabaja lleva la clase que ilumina el borde
            assert app._cards["curador"].has_class("running")
            assert app._cards["orchestrator"].has_class("running")

    asyncio.run(scenario())


def test_finished_agent_is_marked_and_the_panel_hides(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_event(event(E.NODE_STARTED, node_id="n1", payload={"spec": "curador"}))
            app._on_event(
                event(E.NODE_FINISHED, node_id="n1", payload={"status": "ok", "summary": "3 nodos"})
            )
            await pilot.pause()
            assert "hecho" in card_text(app, "curador")
            assert "3 nodos" in card_text(app, "curador")
            assert app._cards["curador"].has_class("done")

            app._on_event(event(E.TASK_FINISHED))
            await pilot.pause()
            assert app.query_one("#agents-panel").display is False  # el log recupera el ancho

    asyncio.run(scenario())


def test_a_failing_agent_is_marked_as_failed(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_event(event(E.NODE_STARTED, node_id="n1", payload={"spec": "curator"}))
            app._on_event(
                event(
                    E.NODE_FINISHED,
                    node_id="n1",
                    payload={"status": "failed", "summary": "el validador lo rechazó"},
                )
            )
            await pilot.pause()
            assert "fallo" in card_text(app, "curator")
            assert app._cards["curator"].has_class("failed")

    asyncio.run(scenario())


def test_a_new_task_clears_the_previous_rectangles(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_event(event(E.NODE_STARTED, node_id="n1", payload={"spec": "viejo"}))
            await pilot.pause()
            assert len(app.query(".agent-card")) == 1

            app._on_event(event(E.TASK_STARTED))
            await pilot.pause()
            assert len(app.query(".agent-card")) == 0
            assert app.query_one("#agents-panel").display is False

    asyncio.run(scenario())


def test_a_delegated_agent_gets_its_own_rectangle(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._on_event(
                event(E.DELEGATION_STARTED, payload={"agent_id": "sub_agente", "depth": 1})
            )
            await pilot.pause()
            assert "recibiendo delegación" in card_text(app, "sub_agente")

            app._on_event(
                event(E.DELEGATION_COMPLETED, payload={"agent_id": "sub_agente", "ok": True})
            )
            await pilot.pause()
            assert app._cards["sub_agente"].has_class("done")

    asyncio.run(scenario())
