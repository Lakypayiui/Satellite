"""Tests headless de la app Textual (Fase TUI)."""

import asyncio
import json
from pathlib import Path

from satellite.approval.base import ApprovalContext
from satellite.tui.app import ApprovalScreen, SatelliteTUI
from satellite.tui.session import Session
from textual.widgets import Button, Input


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


async def _press(prompt: Input, pilot, text: str) -> None:
    prompt.value = text
    await pilot.press("enter")
    await pilot.pause()


def test_app_mounts_and_answers_help(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            await _press(prompt, pilot, "/help")
            assert any("Comandos disponibles" in entry for entry in app.transcript)
            await _press(prompt, pilot, "/agents")
            assert any(entry.startswith("agents=") for entry in app.transcript)
            await _press(prompt, pilot, "/stats")
            assert any(entry.startswith("ahorro_total=") for entry in app.transcript)

    asyncio.run(scenario())


def test_app_unknown_command(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            await _press(prompt, pilot, "/nope")
            assert any("desconocido" in entry for entry in app.transcript)

    asyncio.run(scenario())


def test_app_chats_with_the_agent(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            await _press(prompt, pilot, "¿qué es la fotosíntesis?")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert any(entry.startswith("respuesta ") for entry in app.transcript)

    asyncio.run(scenario())


def test_app_multi_turn_and_new_conversation(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            await _press(prompt, pilot, "necesito agregar login")
            await app.workers.wait_for_complete()
            await _press(prompt, pilot, "revisa esto")
            await app.workers.wait_for_complete()
            await _press(prompt, pilot, "/history")
            assert "history=4" in app.transcript
            await _press(prompt, pilot, "/new")
            await _press(prompt, pilot, "/history")
            assert "history=0" in app.transcript

    asyncio.run(scenario())


def test_app_approval_modal_resolves_request(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            context = ApprovalContext(kind="agent", proposal_id="p1", summary="crear x", details={})
            task = asyncio.ensure_future(app.approval.request({}, context))
            # el timer abre el modal
            for _ in range(30):
                await pilot.pause(0.05)
                if isinstance(app.screen, ApprovalScreen):
                    break
            assert isinstance(app.screen, ApprovalScreen)
            app.screen.query_one("#approve", Button).press()
            decision = await asyncio.wait_for(task, timeout=5)
            assert decision.status.value == "approved"

    asyncio.run(scenario())


def test_app_skill_commands(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        skills_dir = tmp_path / "home" / "skills" / "count_words"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "manifest.json").write_text(
            json.dumps({"id": "count_words", "description": "cuenta palabras", "entrypoint": "main.py"}),
            encoding="utf-8",
        )
        (skills_dir / "main.py").write_text("print(1)", encoding="utf-8")
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            await _press(prompt, pilot, "/skills")
            assert any(entry.startswith("skills=") for entry in app.transcript)
            await _press(prompt, pilot, "/skill-search contar palabras")
            assert any(entry.startswith("search=") for entry in app.transcript)
            await _press(prompt, pilot, "/skill-delete count_words")
            assert any(entry.startswith("borrada") for entry in app.transcript)

    asyncio.run(scenario())


def test_app_plan_mode_choose_option(tmp_path, monkeypatch) -> None:
    from satellite.core.plan import PlanOption, PlanOptions
    from satellite.orchestration.plan_mode import PlanOptionGenerator
    from satellite.tui.app import PlanOptionsScreen

    async def scenario():
        async def fake_generate(self, task, *, context="", history="", feedback=""):
            return PlanOptions(
                question="¿Cómo lo hacemos?",
                options=(
                    PlanOption(id="minimo", title="Mínimo", summary="cambio pequeño", approach=("paso a",)),
                    PlanOption(id="robusto", title="Robusto", summary="solución completa", approach=("paso b",)),
                ),
                recommendation="minimo",
            )

        monkeypatch.setattr(PlanOptionGenerator, "generate", fake_generate)
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            await _press(prompt, pilot, "/plan necesito agregar login")
            await app.workers.wait_for_complete()
            for _ in range(30):
                await pilot.pause(0.05)
                if isinstance(app.screen, PlanOptionsScreen):
                    break
            assert isinstance(app.screen, PlanOptionsScreen)
            assert any(entry.startswith("opciones=") for entry in app.transcript)
            app.screen.query_one("#opt-minimo", Button).press()
            await pilot.pause(0.05)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert any(entry.startswith("opción ") for entry in app.transcript)

    asyncio.run(scenario())


def test_app_quit_command(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/quit"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
