"""Fase A del plan de correcciones (docs/PLAN-correcciones.md).

1. Un agente sólo ejecuta las tools que se le ofrecieron (también en modo plan).
7. Una tool desconocida no tumba el turno y todo turno termina con TASK_FINISHED.
8. El resumen de memoria del turno no retrasa la entrega de la respuesta.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.core.events import EventType
from satellite.orchestration.chat import ChatSession
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


class _SlowSummarizer(FakeLLM):
    """Resumidor del rol ``context`` con latencia, para medir si bloquea el turno."""

    def __init__(self, delay: float = 0.0) -> None:
        super().__init__(default_content='{"user_summary": "u", "assistant_summary": "a"}')
        self.delay = delay

    async def complete(self, request):
        await asyncio.sleep(self.delay)
        return await super().complete(request)


def _runtime(tmp_path: Path, monkeypatch, script: list[dict], *, summary_delay: float = 0.0):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m", "policy_mode": "auto"}), encoding="utf-8"
    )
    provider = FakeLLM(script=script, default_content="")
    summarizer = _SlowSummarizer(summary_delay)
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: summarizer if role == "context" else default,
    )
    orchestrator, bus, _store, _registry = runtime_builder.build_runtime(project)
    events: list = []
    bus.subscribe(events.append)
    return project, orchestrator, events, provider, summarizer


def _call(name: str, **arguments) -> dict:
    return {"tool_calls": [{"id": "c1", "name": name, "arguments": arguments}]}


# --- 1 · sólo las tools ofrecidas -----------------------------------------------------


def test_plan_mode_cannot_write_even_if_the_model_asks(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, _events, provider, _s = _runtime(
        tmp_path,
        monkeypatch,
        [_call("write_file", path="hecho.txt", content="x"), {"content": "Propuesta lista."}],
    )

    reply = run(ChatSession(project, orchestrator).send("investiga", plan_mode=True))

    assert not (project / "hecho.txt").exists()
    assert reply.status == "ok"
    # El modelo ve el rechazo y puede seguir.
    tool_message = provider.requests[1].messages[-1].content
    assert "tool_not_offered" in tool_message or "not offered" in tool_message


def test_plan_mode_cannot_delegate(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, events, _provider, _s = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call("delegate", agent_id="python_coder", task={"goal": "edita app.py"}),
            {"content": "ok"},
        ],
    )

    run(ChatSession(project, orchestrator).send("investiga", plan_mode=True))

    kinds = [event.event_type for event in events]
    assert EventType.DELEGATION_STARTED not in kinds


def test_an_agent_cannot_use_a_tool_outside_its_spec(tmp_path: Path, monkeypatch) -> None:
    _project, orchestrator, _events, _provider, _s = _runtime(tmp_path, monkeypatch, [])
    factory = orchestrator.services.factory
    auditor = factory.create("python_auditor")
    coder = factory.create("python_coder")
    offered = {tool.id for tool in auditor.tools}

    from satellite.core.tool_models import ToolCall

    if "write_file" not in offered:
        denial = auditor._offered_tool_denial(ToolCall(id="c", name="write_file", arguments={}))
        assert denial is not None and denial[1] == "tool_not_offered"
    assert coder._offered_tool_denial(ToolCall(id="c", name="read_file", arguments={})) is None


# --- 7 · tool desconocida y cierre del turno ------------------------------------------


def test_an_unknown_tool_is_reported_to_the_model_and_the_turn_goes_on(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, events, provider, _s = _runtime(
        tmp_path, monkeypatch, [_call("no_existe", x=1), {"content": "Corregido."}]
    )

    reply = run(ChatSession(project, orchestrator).send("hola"))

    assert reply.status == "ok"
    assert reply.text == "Corregido."
    assert "no_existe" in provider.requests[1].messages[-1].content
    assert [e.event_type for e in events].count(EventType.TASK_FINISHED) == 1


def test_the_registry_reports_an_unknown_tool_instead_of_raising(tmp_path: Path) -> None:
    from satellite.core.tool_models import ToolCall
    from satellite.tools.base import ToolContext
    from satellite.tools.registry import DefaultToolRegistry

    result = run(
        DefaultToolRegistry().execute(
            ToolCall(id="c", name="fantasma", arguments={}), ToolContext(project_root=tmp_path)
        )
    )

    assert result.success is False
    assert result.error_code == "unknown_tool"


def test_any_crash_in_the_turn_still_emits_task_finished(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, events, _provider, _s = _runtime(tmp_path, monkeypatch, [])

    async def boom(*_args, **_kwargs):
        raise RuntimeError("fallo inesperado")

    factory = orchestrator.services.factory
    real_create = factory.create

    def create(spec_id, **kwargs):
        agent = real_create(spec_id, **kwargs)
        agent.execute = boom
        return agent

    monkeypatch.setattr(factory, "create", create)

    reply = run(ChatSession(project, orchestrator).send("hola"))

    assert reply.status == "failed"
    assert "fallo inesperado" in reply.text
    kinds = [e.event_type for e in events]
    assert kinds[0] is EventType.TASK_STARTED
    assert kinds[-1] is EventType.TASK_FINISHED


# --- 8 · la memoria no bloquea la entrega ---------------------------------------------


def test_the_turn_summary_does_not_delay_the_reply(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, _events, _provider, summarizer = _runtime(
        tmp_path, monkeypatch, [{"content": "respuesta"}], summary_delay=1.0
    )
    chat = ChatSession(project, orchestrator)

    async def scenario() -> float:
        started = time.monotonic()
        await chat.send("hola qué tal")
        elapsed = time.monotonic() - started
        await chat.flush_background()
        return elapsed

    elapsed = run(scenario())

    assert elapsed < 0.5
    assert len(summarizer.requests) == 1


def test_the_next_turn_sees_the_previous_summary(tmp_path: Path, monkeypatch) -> None:
    from satellite.context.turn_summaries import load_turn_summaries

    project, orchestrator, _events, _provider, _s = _runtime(
        tmp_path,
        monkeypatch,
        [{"content": "uno"}, {"content": "dos"}],
        summary_delay=0.2,
    )
    chat = ChatSession(project, orchestrator)

    async def scenario() -> None:
        await chat.send("primero")
        await chat.send("segundo")
        await chat.flush_background()

    run(scenario())

    assert len(load_turn_summaries(project, chat.session_id)) == 2


def test_a_quick_second_message_does_not_wait_for_the_first_summary(
    tmp_path: Path, monkeypatch
) -> None:
    from satellite.context.turn_summaries import load_turn_summaries

    project, orchestrator, _events, _provider, summarizer = _runtime(
        tmp_path,
        monkeypatch,
        [{"content": "uno"}, {"content": "dos"}],
        summary_delay=1.0,
    )
    chat = ChatSession(project, orchestrator)

    async def scenario() -> float:
        started = time.monotonic()
        await chat.send("primero")
        await chat.send("segundo")
        elapsed = time.monotonic() - started
        await chat.flush_background()
        return elapsed

    elapsed = run(scenario())

    # El turno anterior ya va literal en el historial: no hace falta esperar su resumen.
    assert elapsed < 0.8
    entries = load_turn_summaries(project, chat.session_id)
    assert [entry.get("task_id") for entry in entries] == [
        turn.task_id for turn in chat.history if turn.role == "assistant"
    ]


def test_a_late_summary_is_saved_in_the_session_it_belongs_to(
    tmp_path: Path, monkeypatch
) -> None:
    from satellite.context.turn_summaries import load_turn_summaries

    project, orchestrator, _events, _provider, _s = _runtime(
        tmp_path, monkeypatch, [{"content": "uno"}], summary_delay=0.3
    )
    chat = ChatSession(project, orchestrator)

    async def scenario() -> tuple[str, str]:
        await chat.send("primero")
        first = chat.session_id
        second = chat.new_session("otra")
        await chat.flush_background()
        return first, second

    first, second = run(scenario())

    assert len(load_turn_summaries(project, first)) == 1
    assert load_turn_summaries(project, second) == []


def test_a_delegate_runs_in_the_same_session_as_the_pilot(tmp_path: Path, monkeypatch) -> None:
    coder_output = {"summary": "s", "files_changed": [], "verification": {"passed": True}}
    project, orchestrator, _events, _provider, _s = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call("delegate", agent_id="python_coder", task={"goal": "edit app.py"}),
            {"content": json.dumps(coder_output)},
            {"content": "ok"},
        ],
    )
    seen: list[str] = []
    factory = orchestrator.services.factory
    real_create = factory.create

    def create(spec_id, **kwargs):
        agent = real_create(spec_id, **kwargs)
        if spec_id == "python_coder":
            original = agent.execute

            async def execute(task, ctx):
                seen.append(ctx.session_id)
                return await original(task, ctx)

            agent.execute = execute
        return agent

    monkeypatch.setattr(factory, "create", create)
    chat = ChatSession(project, orchestrator)

    run(chat.send("cambia app.py"))

    assert seen == [chat.session_id] and chat.session_id
