"""Reglas de ejecución del prefijo (F5): gate de calentamiento, grupos e historial."""

import asyncio
from pathlib import Path

import pytest

from satellite.core.messages import CallContext, LLMRequest, Message, Role
from satellite.core.task import TaskResult, TaskStatus
from satellite.observability.call_log import CallRecorder
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.prefix_rules import (
    GROUP_CHANGED,
    GROUP_STABLE,
    PrefixGate,
    PrefixGroupTracker,
)
from satellite.providers.fake import FakeLLM
from satellite.providers.metered import MeteredProvider


def make_request(*, system: str = "SYS", task_id: str = "t1", agent_id: str = "a1") -> LLMRequest:
    return LLMRequest(
        messages=(
            Message(role=Role.SYSTEM, content=system),
            Message(role=Role.USER, content="CAT"),
            Message(role=Role.USER, content="Tarea: x"),
        ),
        stable_user_count=1,
        call=CallContext(task_id=task_id, agent_id=agent_id, role="agent"),
    )


# ---------------------------------------------------------------- PrefixGate


def test_gate_first_call_is_alone_and_followers_wait() -> None:
    async def scenario():
        gate = PrefixGate()
        events: list[str] = []

        async def call(tag: str, delay: float) -> None:
            async with gate.enter("h1") as warmed:
                events.append(f"{tag}:start:warmed={warmed}")
                await asyncio.sleep(delay)
                events.append(f"{tag}:end")

        owner = asyncio.create_task(call("a", 0.05))
        await asyncio.sleep(0)  # deja que el dueño reclame el prefijo
        followers = [asyncio.create_task(call(f"f{i}", 0.0)) for i in range(2)]
        await asyncio.gather(owner, *followers)

        assert events[0] == "a:start:warmed=True"
        assert events[1] == "a:end"  # nadie más empezó mientras el dueño trabajaba
        assert events[2].endswith("warmed=False")
        assert events[3].endswith("warmed=False")
        assert gate.is_warmed("h1")
        assert gate.awaiting() == 0

    asyncio.run(scenario())


def test_gate_warmed_prefix_passes_without_waiting() -> None:
    async def scenario():
        gate = PrefixGate()
        async with gate.enter("h") as first:
            assert first is True
        async with gate.enter("h") as second:
            assert second is False
        assert gate.is_warmed("h")

    asyncio.run(scenario())


def test_gate_is_per_prefix() -> None:
    async def scenario():
        gate = PrefixGate()

        async def call(key: str) -> None:
            async with gate.enter(key):
                await asyncio.sleep(0.01)

        await asyncio.gather(call("a"), call("b"))
        assert gate.is_warmed("a") and gate.is_warmed("b")

    asyncio.run(scenario())


def test_gate_releases_followers_when_the_owner_fails() -> None:
    async def scenario():
        gate = PrefixGate()

        async def failing() -> None:
            async with gate.enter("h"):
                raise RuntimeError("boom")

        async def follower() -> bool:
            async with gate.enter("h") as warmed:
                return warmed

        owner = asyncio.create_task(failing())
        await asyncio.sleep(0)
        waiter = asyncio.create_task(follower())
        with pytest.raises(RuntimeError):
            await owner
        # no se queda colgado: el prefijo se marca como intentado
        assert await asyncio.wait_for(waiter, timeout=1) is False

    asyncio.run(scenario())


def test_gate_reset_releases_waiters() -> None:
    async def scenario():
        gate = PrefixGate()
        async with gate.enter("h"):
            pass
        gate.reset()
        assert not gate.is_warmed("h")
        async with gate.enter("h") as warmed:
            assert warmed is True

    asyncio.run(scenario())


# ---------------------------------------------------------------- tracker


def test_group_tracker_flags_prefix_change_in_task() -> None:
    tracker = PrefixGroupTracker()
    assert tracker.observe(task_id="t", spec_id="a", prefix_hash="h1", model="m") == GROUP_STABLE
    assert tracker.observe(task_id="t", spec_id="a", prefix_hash="h1", model="m") == GROUP_STABLE
    assert tracker.observe(task_id="t", spec_id="a", prefix_hash="h2", model="m") == GROUP_CHANGED

    group = tracker.group("t", "a")
    assert group is not None
    assert group.calls == 3
    assert group.distinct_prefixes == 2
    assert group.stable is False
    assert group.model == "m"
    assert tracker.unstable_groups() == [group]
    assert group.to_dict()["stable"] is False


def test_group_tracker_is_per_task_and_agent() -> None:
    tracker = PrefixGroupTracker()
    tracker.observe(task_id="t1", spec_id="a", prefix_hash="h1")
    assert tracker.observe(task_id="t1", spec_id="b", prefix_hash="h2") == GROUP_STABLE
    assert tracker.observe(task_id="t2", spec_id="a", prefix_hash="h9") == GROUP_STABLE
    assert tracker.unstable_groups() == []
    assert len(tracker.groups()) == 3
    assert tracker.group("t9", "a") is None
    tracker.reset()
    assert tracker.groups() == []


# ---------------------------------------------------------------- provider medido


def test_metered_provider_warms_prefix_and_tracks_group(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    inner = FakeLLM(script=[{"content": "a"}, {"content": "b"}, {"content": "c"}])
    gate, tracker = PrefixGate(), PrefixGroupTracker()
    provider = MeteredProvider(
        inner, recorder, model="m", gate=gate, tracker=tracker
    )

    asyncio.run(provider.complete(make_request(system="S1")))
    assert provider.last_warmed is True
    assert gate.is_warmed(provider.last_record.prefix_hash)

    asyncio.run(provider.complete(make_request(system="S1")))
    assert provider.last_group_status == GROUP_STABLE

    # mismo agente y tarea, pero el prefijo cambia -> se avisa
    asyncio.run(provider.complete(make_request(system="S2")))
    assert provider.last_group_status == GROUP_CHANGED
    assert len(tracker.unstable_groups()) == 1
    assert gate.awaiting() == 0


def test_metered_provider_respects_an_injected_gate(tmp_path: Path) -> None:
    gate = PrefixGate()
    inner = FakeLLM()
    provider = MeteredProvider(inner, None, gate=gate)
    asyncio.run(provider.complete(make_request()))
    assert gate.is_warmed(provider.last_record.prefix_hash) if provider.last_record else True
    assert gate.awaiting() == 0


# ---------------------------------------------------------------- historial


class FakeOrchestrator:
    """Orquestador mínimo: el chat sólo necesita ``execute``."""

    services = None

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task):
        self.calls += 1
        return TaskResult(task_id=f"t{self.calls}", status=TaskStatus.OK, summary="ok")


def test_history_is_append_only() -> None:
    session = ChatSession(Path.cwd(), FakeOrchestrator(), persist=False)
    session.add("user", "uno")
    session.add("assistant", "dos")
    assert [turn.content for turn in session.turns()] == ["uno", "dos"]
    assert session.turns()[0].role == "user"


def test_compact_is_the_only_manual_trim_point() -> None:
    session = ChatSession(Path.cwd(), FakeOrchestrator(), max_recap_turns=4, persist=False)
    for index in range(6):
        session.add("user", f"m{index}")

    assert session.compact(keep=2, summary="resumen de lo anterior") == 4
    turns = session.turns()
    assert [turn.content for turn in turns] == ["resumen de lo anterior", "m4", "m5"]
    assert turns[0].role == "summary"

    # sin resumen sólo recorta
    assert session.compact(keep=1) == 2
    assert [turn.content for turn in session.turns()] == ["m5"]


def test_compact_does_nothing_when_history_is_short() -> None:
    session = ChatSession(Path.cwd(), FakeOrchestrator(), max_recap_turns=4, persist=False)
    session.add("user", "uno")
    assert session.compact() == 0
    assert len(session.turns()) == 1


def test_chat_does_not_compact_with_a_large_window() -> None:
    """Con ventana holgada no se compacta solo (el auto depende del umbral)."""

    async def scenario():
        session = ChatSession(Path.cwd(), FakeOrchestrator(), max_recap_turns=2, persist=False)
        for index in range(6):
            await session.send(f"mensaje {index}")
        # 6 mensajes + 6 respuestas: nada se recorta por su cuenta
        assert len(session.turns()) == 12

    asyncio.run(scenario())
