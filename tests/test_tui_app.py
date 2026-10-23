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
        json.dumps({"name": "fake", "model": "m"}),
        encoding="utf-8",
    )
    return SatelliteTUI(Session(tmp_path, home=home))


def test_the_approval_poll_survives_an_app_without_screens(tmp_path, monkeypatch) -> None:
    """El intervalo de aprobaciones dispara también al arrancar y al cerrar: sin pila de
    pantallas no debe lanzar (era la causa de los fallos intermitentes de la TUI)."""
    app = make_app(tmp_path, monkeypatch)
    app.approval.pending = lambda: [{"proposal_id": "p1", "summary": "s"}]  # type: ignore[method-assign]

    app._poll_approvals()  # fuera de run_test: no hay pantallas ni bucle


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
            # el timer abre el modal; hay que esperar a que el botón exista, no sólo
            # a que la pantalla sea ApprovalScreen (push_screen la activa antes de
            # componer y ahí query_one("#approve") podía reventar: era un flake).
            for _ in range(30):
                await pilot.pause(0.05)
                if isinstance(app.screen, ApprovalScreen) and app.screen.query("#approve"):
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


def test_a_command_request_offers_allowing_it_for_the_session(tmp_path, monkeypatch) -> None:
    """Un comando también se puede permitir una vez o para la sesión."""
    from satellite.core.proposal import ApprovalStatus
    from satellite.tui.app import ApprovalScreen

    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            tomada: dict = {}
            app.push_screen(
                ApprovalScreen(
                    "p1",
                    "ejecutar: git status --short",
                    {"command": "git status --short", "tool": "run_command", "consequence": "ejecución"},
                    "command",
                ),
                lambda decision: tomada.setdefault("d", decision),
            )
            await pilot.pause()
            app.screen.query_one("#approve-session", Button).press()
            await pilot.pause()
            assert tomada["d"].status is ApprovalStatus.APPROVED
            assert tomada["d"].feedback == "session"

    asyncio.run(scenario())


def test_a_path_request_offers_allowing_it_for_the_session(tmp_path, monkeypatch) -> None:
    """Una ruta de fuera del proyecto se puede permitir una vez o para la sesión."""
    from satellite.core.proposal import ApprovalStatus
    from satellite.tui.app import ApprovalScreen

    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            tomada: dict = {}
            app.push_screen(
                ApprovalScreen(
                    "p1",
                    "read_file pide acceso a C:/otra/carpeta",
                    {"path": "C:/otra/carpeta", "tool": "read_file", "consequence": "lectura"},
                    "path",
                ),
                lambda decision: tomada.setdefault("d", decision),
            )
            await pilot.pause()
            app.screen.query_one("#approve-session", Button).press()
            await pilot.pause()
            assert tomada["d"].status is ApprovalStatus.APPROVED
            assert tomada["d"].feedback == "session"

            # en una petición normal (agente/skill) ese botón no aparece
            app.push_screen(ApprovalScreen("p2", "crear agente x", {}, "agent"), lambda d: None)
            await pilot.pause()
            assert not app.screen.query("#approve-session")
            app.screen.query_one("#approve", Button).press()

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
