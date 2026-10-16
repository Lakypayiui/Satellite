"""Eventos del backend: reparto en vivo, reanudación por índice y persistencia.

El flujo SSE se comprueba a nivel del generador (`event_stream`), que es donde está la
lógica; abrirlo a través del cliente de pruebas de FastAPI se queda esperando una
conexión viva y no aporta nada que no cubran estas pruebas. El camino HTTP completo se
verifica a mano con ``curl`` (está en el README).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.core.events import EventType, ExecutionEvent  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.desktop.events import EventBridge, event_stream  # noqa: E402
from satellite.events.bus import EventBus  # noqa: E402

TOKEN = "token-eventos"


class RecordingStore:
    """Almacén que apunta lo persistido (o que falla, si se pide)."""

    def __init__(self, *, roto: bool = False) -> None:
        self.events: list[tuple[str, ExecutionEvent]] = []
        self.roto = roto

    async def append_event(self, task_id: str, event: ExecutionEvent) -> None:
        if self.roto:
            raise OSError("disco lleno")
        self.events.append((task_id, event))


class FakeSession:
    """Sólo lo que el puente necesita."""

    def __init__(self, root, home=None) -> None:
        self.root = Path(root)
        self.bus = EventBus()
        self.persistence = RecordingStore()

    def runtime(self):
        return (None, self.bus, self.persistence, None)


def evento(tipo: EventType = EventType.NODE_STARTED, task_id: str = "t1") -> ExecutionEvent:
    return ExecutionEvent(event_type=tipo, task_id=task_id, payload={"spec": "eco"})


def run(coro):
    return asyncio.run(coro)


def make_client(tmp_path: Path, *, factory=FakeSession) -> tuple[TestClient, DesktopBackend]:
    backend = DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=factory,
        recents_path=tmp_path / "recents.json",
    )
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return client, backend


# --- el puente ---


def test_a_numbered_stream_lets_a_client_resume_where_it_left() -> None:
    bus = EventBus()
    bridge = EventBridge()
    bridge.attach(bus, RecordingStore())

    bus.emit(evento(task_id="t1"))
    bus.emit(evento(EventType.NODE_FINISHED, task_id="t1"))

    assert bridge.last_index == 2
    assert [item["index"] for item in bridge.since(0)] == [1, 2]
    assert [item["index"] for item in bridge.since(1)] == [2]
    assert bridge.since(2) == []


def test_events_are_persisted_for_history() -> None:
    """Se persisten desde dentro del bucle, que es como los emite el runtime."""
    bus = EventBus()
    store = RecordingStore()
    bridge = EventBridge()
    bridge.attach(bus, store)

    async def escenario():
        bus.emit(evento(task_id="t9"))  # el runtime emite siempre con bucle en marcha
        await bridge.drain()

    run(escenario())

    assert [item[0] for item in store.events] == ["t9"]
    assert store.events[0][1].event_type is EventType.NODE_STARTED


def test_a_broken_store_does_not_break_the_agent() -> None:
    bus = EventBus()
    bridge = EventBridge()
    bridge.attach(bus, RecordingStore(roto=True))

    async def escenario():
        bus.emit(evento())
        await bridge.drain()

    run(escenario())  # no debe explotar

    assert bridge.last_index == 1
    assert bridge.since(0)[0]["event"]["event_type"] == "node_started"


def test_without_a_store_nothing_is_persisted_but_the_stream_still_works() -> None:
    bus = EventBus()
    bridge = EventBridge()
    bridge.attach(bus)

    bus.emit(evento())

    assert bridge.since(0)[0]["event"]["event_type"] == "node_started"


# --- el flujo SSE ---


def test_the_stream_sends_what_already_happened_and_then_stops() -> None:
    bus = EventBus()
    bridge = EventBridge()
    bridge.attach(bus, RecordingStore())
    bus.emit(evento())

    async def leer():
        stop = asyncio.Event()
        generador = event_stream(bridge, 0, heartbeat_s=0.05, stop=stop)
        primero = await generador.__anext__()
        stop.set()
        return primero

    primero = run(leer())

    assert primero.startswith("data: ")
    assert "node_started" in primero


def test_the_stream_sends_a_heartbeat_when_nothing_happens() -> None:
    bridge = EventBridge()
    bridge.attach(EventBus(), RecordingStore())

    async def leer():
        stop = asyncio.Event()
        generador = event_stream(bridge, 0, heartbeat_s=0.05, stop=stop)
        latido = await generador.__anext__()
        stop.set()
        return latido

    assert run(leer()) == ": latido\n\n"


def test_the_stream_unsubscribes_when_the_client_goes_away() -> None:
    bridge = EventBridge()
    bridge.attach(EventBus(), RecordingStore())

    async def escenario():
        stop = asyncio.Event()
        generador = event_stream(bridge, 0, heartbeat_s=0.05, stop=stop)
        await generador.__anext__()
        stop.set()
        with pytest.raises(StopAsyncIteration):
            await generador.__anext__()
        return list(bridge._queues)

    assert run(escenario()) == []


# --- la puerta del endpoint ---


def test_the_event_endpoint_needs_the_token(tmp_path: Path) -> None:
    client, _backend = make_client(tmp_path)
    client.headers.pop("Authorization")

    respuesta = client.get("/api/events")

    assert respuesta.status_code == 401
