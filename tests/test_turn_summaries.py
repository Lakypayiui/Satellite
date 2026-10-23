"""Memoria persistente por turno: JSON, resumen, inyección y compactación."""

import asyncio
import json
from pathlib import Path

from satellite.context.turn_summaries import (
    ASSISTANT_SUMMARY_MAX_CHARS,
    USER_SUMMARY_MAX_CHARS,
    append_turn_summary,
    load_turn_summaries,
    make_entry,
    render_turn_summaries,
    summaries_path,
    summarize_turn,
)
from satellite.core.task import TaskResult, TaskStatus
from satellite.orchestration.chat import ChatSession, Turn
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


class StubOrchestrator:
    services = None

    def __init__(self) -> None:
        self.tasks: list = []

    async def execute(self, task):
        self.tasks.append(task)
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


def test_append_load_and_render_roundtrip(tmp_path: Path) -> None:
    entry = make_entry(
        user_summary="Pide agregar login",
        assistant_summary="Implementó el login y dejó pendientes los tests",
        task_id="t1",
        status="ok",
    )
    assert append_turn_summary(tmp_path, "s1", entry) is True

    entries = load_turn_summaries(tmp_path, "s1")
    assert len(entries) == 1
    assert entries[0]["user_summary"] == "Pide agregar login"
    assert summaries_path(tmp_path, "s1").name == "s1.json"
    assert summaries_path(tmp_path, "s1").parent.name == "summaries"

    block = render_turn_summaries(entries)
    assert "Pide agregar login" in block
    assert "Implementó el login" in block
    assert "untrusted data" in block


def test_deleting_the_session_deletes_its_memory(tmp_path: Path) -> None:
    from satellite.orchestration.sessions import SessionStore

    store = SessionStore(tmp_path)
    session_id = store.create("con memoria")
    append_turn_summary(
        tmp_path,
        session_id,
        make_entry(user_summary="hola", assistant_summary="mundo"),
    )
    assert summaries_path(tmp_path, session_id).is_file()

    store.delete(session_id)

    assert not summaries_path(tmp_path, session_id).exists()


def test_corrupt_file_is_tolerated(tmp_path: Path) -> None:
    path = summaries_path(tmp_path, "rota")
    path.parent.mkdir(parents=True)
    path.write_text("{no json", encoding="utf-8")
    assert load_turn_summaries(tmp_path, "rota") == []


def test_render_keeps_the_most_recent_entries(tmp_path: Path) -> None:
    entries = [
        {"user_summary": f"usuario {i}", "assistant_summary": f"modelo {i}"}
        for i in range(40)
    ]
    block = render_turn_summaries(entries, max_entries=30, max_chars=10000)
    assert "usuario 39" in block
    assert "usuario 9" not in block  # solo los últimos 30


def test_summarizer_uses_the_model_json_and_clips(tmp_path: Path) -> None:
    provider = FakeLLM(
        script=[
            {
                "content": json.dumps(
                    {
                        "user_summary": "u" * 1000,
                        "assistant_summary": "a" * 2000,
                    }
                )
            }
        ]
    )
    result = run(
        summarize_turn(
            provider,
            user_text="necesito login",
            assistant_text="listo",
            language="es",
        )
    )
    assert len(result["user_summary"]) <= USER_SUMMARY_MAX_CHARS
    assert len(result["assistant_summary"]) <= ASSISTANT_SUMMARY_MAX_CHARS
    assert provider.requests[0].max_tokens == 500
    assert "necesito login" in provider.requests[0].messages[1].content


def test_summarizer_falls_back_without_provider() -> None:
    result = run(
        summarize_turn(
            None,
            user_text="necesito agregar login",
            assistant_text="ok",
        )
    )
    assert "agregar login" in result["user_summary"]
    assert result["assistant_summary"] == "ok"


def test_chat_records_each_turn_and_injects_the_memory(tmp_path: Path) -> None:
    orchestrator = StubOrchestrator()
    chat = ChatSession(tmp_path, orchestrator)

    run(chat.send("necesito agregar login"))
    entries = json.loads(
        summaries_path(tmp_path, chat.session_id).read_text(encoding="utf-8")
    )["entries"]
    assert len(entries) == 1
    assert "agregar login" in entries[0]["user_summary"]
    assert entries[0]["assistant_summary"]

    run(chat.send("ahora agrega los tests"))
    memory = orchestrator.tasks[-1].metadata.get("session_memory", "")
    assert "necesito agregar login" in memory
    assert "ahora agrega los tests" not in memory  # el turno actual aún no se resume


def test_the_pilot_context_receives_the_memory(tmp_path: Path) -> None:
    from satellite.core.context_models import ContextBundle
    from satellite.core.task import Task
    from satellite.orchestration.turn_context import with_conversation

    task = Task(
        goal="continúa",
        project_root=tmp_path,
        metadata={"session_memory": "MEMORIA-PERSISTENTE-XYZ"},
    )
    bundle = with_conversation(ContextBundle(), task)
    assert any(
        "MEMORIA-PERSISTENTE-XYZ" in section.content for section in bundle.sections
    )


def test_compaction_uses_the_persistent_summaries() -> None:
    from satellite.context.compaction import summarize_turns

    provider = FakeLLM(script=[{"content": "resumen compactado"}])
    text, method = run(
        summarize_turns(
            [Turn(role="user", content="hola " * 200)],
            provider,
            persistent_summary="MEMORIA-PERSISTENTE-XYZ",
        )
    )
    assert method == "llm"
    assert text == "resumen compactado"
    enviado = "\n".join(message.content for message in provider.requests[0].messages)
    assert "MEMORIA-PERSISTENTE-XYZ" in enviado
