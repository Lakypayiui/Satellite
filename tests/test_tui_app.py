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


def test_plan_modal_makes_all_the_options_reachable(tmp_path, monkeypatch) -> None:
    """Con 4 alternativas los 5 botones tienen que caber dentro de la caja.

    Iban en un `Horizontal` sin wrap dentro de una caja de ancho fijo: los de la
    derecha se salían y no se podían elegir (solo se veían 2 de 4).
    """
    from satellite.core.plan import PlanOption, PlanOptions
    from satellite.orchestration.plan_mode import PlanOptionGenerator
    from satellite.tui.app import PlanOptionsScreen

    async def scenario():
        async def fake_generate(self, task, *, context="", history="", feedback=""):
            return PlanOptions(
                question="¿Cómo lo hacemos?",
                options=tuple(
                    PlanOption(
                        id=f"o{i}",
                        title=f"Alternativa {i}",
                        summary="s",
                        approach=(f"p{i}",),
                    )
                    for i in range(1, 5)
                ),
                recommendation="o1",
            )

        monkeypatch.setattr(PlanOptionGenerator, "generate", fake_generate)
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            await _press(prompt, pilot, "/plan necesito agregar login")
            await app.workers.wait_for_complete()
            for _ in range(30):
                await pilot.pause(0.05)
                if isinstance(app.screen, PlanOptionsScreen):
                    break
            assert isinstance(app.screen, PlanOptionsScreen)
            await pilot.pause()

            box = app.screen.query_one("#plan-box")
            for bid in ("opt-o1", "opt-o2", "opt-o3", "opt-o4", "opt-other"):
                boton = app.screen.query_one(f"#{bid}", Button)
                assert boton.region.x >= box.region.x, bid
                assert boton.region.right <= box.region.right, bid
                assert boton.region.y >= box.region.y, bid

            # elegir por número cierra el modal sin depender del ratón
            await pilot.press("3")
            await pilot.pause(0.05)
            await app.workers.wait_for_complete()
            assert not isinstance(app.screen, PlanOptionsScreen)

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


def test_the_number_keys_pick_the_right_option(monkeypatch) -> None:
    from satellite.core.plan import PlanOption, PlanOptions
    from satellite.tui.app import PlanOptionsScreen

    options = PlanOptions(
        question="q",
        options=tuple(PlanOption(id=f"o{i}", title=f"t{i}", summary="s") for i in range(1, 5)),
    )
    elegido: dict = {}
    monkeypatch.setattr(
        PlanOptionsScreen, "dismiss", lambda self, value: elegido.setdefault("v", value)
    )
    screen = PlanOptionsScreen(options)
    screen.action_pick(3)
    assert elegido["v"] == "o3"
    screen.action_pick(9)  # fuera de rango: ni elige ni revienta
    assert elegido["v"] == "o3"


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
