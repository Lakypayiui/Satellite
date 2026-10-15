"""Tests headless del modal de proveedor de la TUI."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from textual.widgets import Input, OptionList

from satellite.config import catalog
from satellite.config.models import ModelsResult
from satellite.tui.app import SatelliteTUI
from satellite.tui.provider_screen import ProviderScreen
from satellite.tui.session import Session


def make_app(tmp_path: Path, monkeypatch) -> SatelliteTUI:
    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
    )
    return SatelliteTUI(Session(tmp_path, home=home))


def read_config(tmp_path: Path) -> dict:
    path = tmp_path / ".satellite" / "config" / "config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def option(key: str) -> SimpleNamespace:
    """Evento falso de OptionList.OptionSelected."""
    return SimpleNamespace(option=SimpleNamespace(id=key))


async def press(prompt: Input, pilot, text: str) -> None:
    prompt.value = text
    await pilot.press("enter")
    await pilot.pause()


async def open_provider(app: SatelliteTUI, pilot) -> ProviderScreen:
    await press(app.query_one("#prompt", Input), pilot, "/provider")
    screen = app.screen
    assert isinstance(screen, ProviderScreen)
    return screen


def test_provider_command_lists_catalog(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)
            options = screen.query_one("#provider-apps", OptionList)
            # todo el catálogo + la opción de URL manual
            assert options.option_count == len(catalog.CATALOG) + 1

    asyncio.run(scenario())


def test_provider_search_filters_catalog(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)
            screen._fill_apps("groq")
            options = screen.query_one("#provider-apps", OptionList)
            assert options.option_count == 2  # groq + manual

    asyncio.run(scenario())


def test_provider_pick_fills_url_and_family(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)
            screen._pick_app(option("groq"))
            await pilot.pause()
            assert screen.query_one("#provider-url", Input).value == "https://api.groq.com/openai/v1"
            assert screen._family() == catalog.OPENAI
            # anthropic mantiene su familia
            screen._pick_app(option("anthropic"))
            assert screen._family() == catalog.ANTHROPIC

    asyncio.run(scenario())


def test_provider_manual_mode_clears_url(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)
            screen._pick_app(option("openai"))
            screen._pick_app(option("manual"))
            await pilot.pause()
            assert screen.entry is None
            assert screen.query_one("#provider-url", Input).value == ""
            assert screen._family() == catalog.OPENAI

    asyncio.run(scenario())


def test_provider_shows_models_and_is_loud_on_failure(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)

            screen._show_models(ModelsResult(ok=True, models=("a", "b"), url="u"))
            await pilot.pause()
            models = screen.query_one("#provider-models", OptionList)
            assert models.display is True
            assert models.option_count == 2

            screen._show_models(
                ModelsResult(ok=False, url="https://x/v1/models", error="HTTP 401")
            )
            await pilot.pause()
            assert "HTTP 401" in screen.status_message
            assert "https://x/v1/models" in screen.status_message
            assert "a mano" in screen.status_message

    asyncio.run(scenario())


def test_provider_save_writes_config_and_closes(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)
            screen._pick_app(option("groq"))
            screen.query_one("#provider-key", Input).value = "gsk-1"
            screen._show_models(ModelsResult(ok=True, models=("llama-3",), url="u"))
            screen.query_one("#provider-model", Input).value = "llama-3"

            screen._save()
            await pilot.pause()
            await pilot.pause()  # el callback del dismiss se procesa en el bucle

            data = read_config(tmp_path)
            assert data["providers"][0]["model"] == "llama-3"
            assert data["providers"][0]["api_key"] == "gsk-1"
            assert data["model"] == "llama-3"  # activo para el runtime
            assert not isinstance(app.screen, ProviderScreen)
            # el callback del modal registra el resultado en el transcript
            assert "provider" in app.transcript

    asyncio.run(scenario())


def test_provider_save_requires_model(tmp_path, monkeypatch) -> None:
    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)
            screen._pick_app(option("groq"))
            screen._save()
            await pilot.pause()
            assert "Falta el modelo" in screen.status_message
            assert isinstance(app.screen, ProviderScreen)  # sigue abierto

    asyncio.run(scenario())


def test_provider_load_models_runs_in_worker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        Session,
        "load_models",
        lambda self, url, family, api_key="": ModelsResult(
            ok=True, models=("m1", "m2"), url=url
        ),
    )

    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await open_provider(app, pilot)
            screen._pick_app(option("ollama"))
            screen._load_models()
            options = screen.query_one("#provider-models", OptionList)
            for _ in range(40):
                await pilot.pause()
                if options.option_count:
                    break
            assert options.option_count == 2

    asyncio.run(scenario())


def test_provider_list_command_shows_table(tmp_path, monkeypatch) -> None:
    from satellite.tui import session as session_module

    async def scenario():
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            await press(app.query_one("#prompt", Input), pilot, "/provider list")
            assert any(entry == "provider-list" for entry in app.transcript)

    asyncio.run(scenario())


def test_session_save_provider_resets_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    session = Session(tmp_path, home=tmp_path / "home")
    session._runtime = ("fake",)  # simula runtime construido
    session.save_provider(
        name="openai",
        model="m",
        base_url="https://x/v1",
        api_key="k",
    )
    assert session._runtime is None
    assert session.active_provider_id()
    assert session.saved_providers()[0].api_key_set is True
