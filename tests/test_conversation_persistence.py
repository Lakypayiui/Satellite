"""Conversación persistida: guardado, recuperación, /new y compactado por CLI."""

import json
from pathlib import Path

from typer.testing import CliRunner

from satellite.cli.app import app
from satellite.core.task import TaskResult, TaskStatus
from satellite.kernel.policy import CompactionConfig, Settings
from satellite.orchestration.chat import ChatSession, conversation_path
from satellite.tui.session import Session


class FakeOrchestrator:
    """Orquestador mínimo: el chat sólo necesita ``execute``."""

    services = None

    def __init__(self) -> None:
        self.histories: list[list] = []

    async def execute(self, task):
        self.histories.append(list((task.metadata or {}).get("history") or []))
        return TaskResult(task_id="t1", status=TaskStatus.OK, summary="ok")


def run(coro):
    import asyncio

    return asyncio.run(coro)


def fill(session: ChatSession, *, pairs: int = 6, long: bool = True) -> None:
    body = "contenido largo " * 25 if long else ""
    for index in range(pairs):
        session.add("user", f"mensaje {index} {body}")
        session.add("assistant", f"respuesta {index} {body}")


# ---------------------------------------------------------------- guardado


def test_conversation_is_saved_and_restored(tmp_path: Path) -> None:
    session = ChatSession(tmp_path, FakeOrchestrator())
    fill(session, pairs=2)
    path = conversation_path(tmp_path)
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["turns"]) == 4
    assert saved["turns"][0]["role"] == "user"
    assert saved["updated_at"]

    fresh = ChatSession(tmp_path, FakeOrchestrator())
    assert fresh.load() == 4
    assert [turn.content for turn in fresh.turns()] == [
        turn.content for turn in session.turns()
    ]
    # y el historial recuperado es el que viaja al orquestador
    run(fresh.send("seguimos"))
    assert len(fresh.turns()) == 6


def test_reset_removes_the_saved_conversation(tmp_path: Path) -> None:
    session = ChatSession(tmp_path, FakeOrchestrator())
    fill(session, pairs=1)
    assert conversation_path(tmp_path).exists()
    session.reset()
    assert not conversation_path(tmp_path).exists()
    assert session.turns() == ()


def test_persist_false_writes_nothing(tmp_path: Path) -> None:
    session = ChatSession(tmp_path, FakeOrchestrator(), persist=False)
    fill(session, pairs=1)
    assert not conversation_path(tmp_path).exists()


def test_load_is_tolerant_to_a_corrupt_file(tmp_path: Path) -> None:
    path = conversation_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{no soy json", encoding="utf-8")
    session = ChatSession(tmp_path, FakeOrchestrator())
    assert session.load() == 0
    # y también con entradas basura dentro
    path.write_text(json.dumps({"turns": [{"role": "user"}, "basura", 3]}), encoding="utf-8")
    assert session.load() == 1


def test_compaction_saves_the_compacted_history(tmp_path: Path) -> None:
    settings = Settings(compaction=CompactionConfig(window=100_000, keep=2))
    session = ChatSession(tmp_path, FakeOrchestrator(), settings=settings)
    fill(session, pairs=6)

    result = run(session.compact_now())
    assert result.dropped > 0
    saved = json.loads(conversation_path(tmp_path).read_text(encoding="utf-8"))
    assert len(saved["turns"]) == 3
    assert saved["turns"][0]["role"] == "summary"


# ---------------------------------------------------------------- TUI


def test_tui_session_remembers_the_conversation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    session = ChatSession(tmp_path, FakeOrchestrator())
    fill(session, pairs=2)

    ui = Session(tmp_path, home=tmp_path / "home")
    monkeypatch.setattr(
        Session, "runtime", lambda self, force=False: (FakeOrchestrator(), None, None, None)
    )
    chat = ui.chat()
    assert len(chat.turns()) == 4
    assert ui.restored_turns() == 4

    ui.new_conversation()  # /new limpia también el fichero
    assert not conversation_path(tmp_path).exists()
    assert ui.conversation() == ()


# ---------------------------------------------------------------- CLI


def test_cli_compact_without_conversation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    result = CliRunner().invoke(app, ["compact", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert "no hay conversación guardada" in result.output


def test_cli_compact_shrinks_the_saved_conversation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    session = ChatSession(tmp_path, FakeOrchestrator())
    fill(session, pairs=6)
    before = conversation_path(tmp_path).read_text(encoding="utf-8")

    result = CliRunner().invoke(
        app, ["compact", "--project", str(tmp_path), "--keep", "2", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    assert "compactado:" in result.output
    assert "resumen (determinista)" in result.output
    after = json.loads(conversation_path(tmp_path).read_text(encoding="utf-8"))
    assert len(after["turns"]) == 3
    assert after["turns"][0]["role"] == "summary"
    assert len(conversation_path(tmp_path).read_text(encoding="utf-8")) < len(before)
    # y el histórico guardado ya lleva el resumen, no los turnos viejos
    assert "mensaje 0" not in json.dumps(after["turns"][-1])
