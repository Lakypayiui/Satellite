"""Backend de escritorio: un turno a la vez, estado del turno y permisos por evento (fase 5)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from satellite.approval.base import ApprovalContext  # noqa: E402
from satellite.core.proposal import ApprovalStatus  # noqa: E402
from satellite.desktop.approvals import decision_from  # noqa: E402
from satellite.desktop.backend import BackendError, DesktopBackend  # noqa: E402
from satellite.events.bus import EventBus  # noqa: E402

TOKEN = "token-turno"


class Store:
    async def append_event(self, task_id: str, event) -> None:
        return None


class SlowSession:
    """Un turno que espera hasta que el test lo suelta."""

    release: asyncio.Event | None = None

    def __init__(self, root, home=None) -> None:
        self.root = Path(root)
        self.bus = EventBus()
        self.persistence = Store()

    def runtime(self, *, force: bool = False):
        return (None, self.bus, self.persistence, None)

    def status(self):
        return {"project_root": str(self.root)}

    async def send_message(self, text, *, approval=None, plan_mode=False, extra_metadata=None):
        await type(self).release.wait()
        return {"text": f"eco: {text}", "task_id": "t1", "status": "ok"}


def make_backend(tmp_path: Path) -> DesktopBackend:
    return DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=SlowSession,
        recents_path=tmp_path / "recents.json",
    )


async def esperar_turno(backend: DesktopBackend) -> None:
    for _ in range(100):
        if backend._turnos:
            return
        await asyncio.sleep(0.01)


def test_a_second_message_is_rejected_while_a_turn_runs(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    async def escenario():
        SlowSession.release = asyncio.Event()
        primero = asyncio.create_task(backend.send("uno"))
        await esperar_turno(backend)
        durante = backend.status()["turn"]
        with pytest.raises(BackendError) as error:
            await backend.send("dos")
        SlowSession.release.set()
        respuesta = await primero
        return durante, error.value, respuesta, backend.status()["turn"]

    durante, error, respuesta, despues = asyncio.run(escenario())

    assert durante["running"] is True and durante["started_at"]
    assert error.code == "turn_running" and error.status == 409
    assert respuesta["text"] == "eco: uno"
    assert despues == {"running": False, "started_at": None}


def test_pending_approvals_are_announced_on_the_event_stream(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    async def escenario():
        bridge = backend.require_runtime()
        cola = bridge.subscribe()
        port = backend.approval()
        ctx = ApprovalContext(kind="command", task_id="t1", summary="ejecutar: ls", details={})
        pedido = asyncio.create_task(port.request({"kind": "command"}, ctx))
        await asyncio.sleep(0)
        proposal_id = port.pending()[0]["proposal_id"]
        port.submit(proposal_id, decision_from("approve", feedback="once"))
        decision = await pedido
        tipos = [cola.get_nowait()["event"]["event_type"] for _ in range(cola.qsize())]
        return decision, tipos

    decision, tipos = asyncio.run(escenario())

    assert decision.status is ApprovalStatus.APPROVED
    assert tipos == ["approval_requested", "approval_resolved"]


def test_requesting_changes_carries_the_users_text() -> None:
    decision = decision_from("needs_changes", reason="usa pytest, no unittest")
    assert decision.status is ApprovalStatus.NEEDS_CHANGES
    assert decision.reason == "usa pytest, no unittest"
