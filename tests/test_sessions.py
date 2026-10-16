"""Sesiones de conversación: crear, nombrar, listar y reanudar."""

import asyncio
import json
from pathlib import Path

from satellite.core.task import TaskResult, TaskStatus
from satellite.orchestration.chat import ChatSession, conversation_path
from satellite.orchestration.sessions import SessionStore


class _Orquestador:
    """Orquestador mínimo: el chat sólo necesita ``execute``."""

    services = None

    async def execute(self, task):
        return TaskResult(task_id="t1", status=TaskStatus.OK, summary="ok")


def run(coro):
    return asyncio.run(coro)


def _chat(tmp_path: Path) -> ChatSession:
    return ChatSession(tmp_path, _Orquestador())


# --- el almacén ---


def test_the_default_session_exists_before_its_file_does(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)

    assert store.active_id() == "default"
    assert store.current_path().name == "default.json"
    assert not store.current_path().exists()  # no se crea nada por mirarlo


def test_the_old_conversation_is_adopted(tmp_path: Path) -> None:
    """Nadie pierde su historial: la conversación de antes pasa a ser la sesión default."""
    legacy = tmp_path / ".satellite" / "state" / "conversation.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"turns": [{"role": "user", "content": "hola"}]}), encoding="utf-8"
    )

    store = SessionStore(tmp_path)

    assert store.current_path().exists()
    assert not legacy.exists()
    _info, turns = store.read("default")
    assert turns[0]["content"] == "hola"


def test_create_lists_and_switches(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    first = store.create("pruebas")
    second = store.create("otra cosa")

    infos = store.list()
    assert infos[0].id == second  # la activa va primero
    assert {info.name for info in infos} == {"pruebas", "otra cosa"}

    assert store.switch("pruebas") == first
    assert store.active_id() == first
    assert store.switch("no-existe") is None


def test_rename_keeps_the_turns(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.create("pruebas")
    store.save(session_id, [{"role": "user", "content": "hola"}], name="pruebas")

    assert store.rename(session_id, "renombrada") is True

    info, turns = store.read(session_id)
    assert info.name == "renombrada"
    assert len(turns) == 1


def test_a_corrupt_session_does_not_explode(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    path = store.session_path("rota")
    path.parent.mkdir(parents=True)
    path.write_text("{no json", encoding="utf-8")

    info, turns = store.read("rota")

    assert info.id == "rota"
    assert turns == []


# --- el chat ---


def test_each_session_keeps_its_own_conversation(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    chat.add("user", "soy de la principal")

    other = chat.new_session("secundaria")
    assert chat.session_id == other
    assert chat.turns() == ()
    chat.add("user", "soy de la secundaria")

    assert chat.use_session("conversación principal") == "default"
    assert [turn.content for turn in chat.turns()] == ["soy de la principal"]

    assert chat.use_session("secundaria") == other
    assert [turn.content for turn in chat.turns()] == ["soy de la secundaria"]


def test_a_new_session_is_empty_and_listed(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    chat.add("user", "hola")

    session_id = chat.new_session()

    assert conversation_path(tmp_path).name == f"{session_id}.json"
    assert chat.turns() == ()
    infos = chat.sessions()
    assert infos[0].id == session_id and infos[0].active is True
    assert infos[0].turns == 0  # la sesión nueva está vacía


def test_rename_through_the_chat_shows_up_in_the_list(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    chat.new_session("provisional")

    assert chat.rename_session("definitiva") is True
    assert chat.sessions()[0].name == "definitiva"
    assert chat.rename_session("   ") is False


def test_the_active_session_survives_a_restart(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    session_id = chat.new_session("persistente")
    chat.add("user", "sigo aquí")

    fresh = _chat(tmp_path)

    assert fresh.session_id == session_id
    assert fresh.load() == 1
    assert fresh.session_name == "persistente"


def test_the_conversation_goes_to_the_active_session_file(tmp_path: Path) -> None:
    chat = _chat(tmp_path)
    session_id = chat.new_session("destino")
    chat.add("user", "dentro")

    guardado = json.loads(
        (tmp_path / ".satellite" / "state" / "conversations" / f"{session_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert guardado["name"] == "destino"
    assert "dentro" in guardado["turns"][0]["content"]
    assert guardado["created_at"] and guardado["updated_at"]


# --- la TUI ---


def test_the_session_commands_create_list_and_resume(tmp_path, monkeypatch) -> None:
    async def scenario():
        import json as _json

        from satellite.tui.app import SatelliteTUI
        from satellite.tui.session import Session

        home = tmp_path / "home"
        monkeypatch.setenv("SATELLITE_HOME", str(home))
        monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
        cfg = tmp_path / ".satellite" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(
            _json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
        )
        app = SatelliteTUI(Session(tmp_path, home=home))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cmd_session("new pruebas")
            app._cmd_session("use no-existe")
            app._cmd_session("name")
            app._cmd_session("")
            app._cmd_sessions("")
            await pilot.pause()
            salida = "\n".join(app.transcript)
            assert "sesión nueva:" in salida
            assert "no existe esa sesión" in salida
            assert "uso: /session name" in salida
            assert "uso: /session <new" in salida
            assert "sessions=1" in salida

    asyncio.run(scenario())
