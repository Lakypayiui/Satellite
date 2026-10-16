"""Cancelación del turno en curso: corta la tarea y no disfraza el resultado."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from satellite.approval.base import ApprovalContext  # noqa: E402
from satellite.core.proposal import ApprovalStatus  # noqa: E402
from satellite.desktop.backend import BackendError, DesktopBackend  # noqa: E402
from satellite.events.bus import EventBus  # noqa: E402

TOKEN = "token-cancelar"


class RecordingStore:
    """Almacén mínimo para el puente."""

    async def append_event(self, task_id: str, event) -> None:
        return None


class SlowSession:
    """Un turno que no termina solo: sólo lo corta la cancelación."""

    def __init__(self, root, home=None) -> None:
        self.root = Path(root)
        self.bus = EventBus()
        self.persistence = RecordingStore()
        self.empezado = asyncio.Event()

    def runtime(self, *, force: bool = False):
        return (None, self.bus, self.persistence, None)

    async def send_message(self, text, *, approval=None, plan_mode=False, extra_metadata=None):
        self.empezado.set()
        await asyncio.Event().wait()  # nunca se pone
        raise AssertionError("no debería llegar aquí")


def make_backend(tmp_path: Path) -> DesktopBackend:
    return DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=SlowSession,
        recents_path=tmp_path / "recents.json",
    )


def test_cancelling_a_turn_returns_a_honest_answer(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    async def escenario():
        turno = asyncio.create_task(backend.send("haz algo largo"))
        for _ in range(100):
            if backend._turnos:
                break
            await asyncio.sleep(0.01)
        pedido = backend.cancel_turn()
        return pedido, await turno

    pedido, respuesta = asyncio.run(escenario())

    assert pedido["cancelled"] is True
    assert respuesta["cancelled"] is True
    assert respuesta["status"] == "cancelled"
    assert "queda en el historial" in respuesta["note"]
    assert backend._turnos == {}  # no queda un turno colgando


def test_cancelling_without_a_turn_says_so(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    with pytest.raises(BackendError) as fallo:
        backend.cancel_turn()

    assert fallo.value.code == "no_turn"
    assert fallo.value.status == 409


def test_cancelling_rejects_the_permissions_that_were_waiting(tmp_path: Path) -> None:
    """Un permiso pendiente ya no tiene a quién preguntar: se rechaza y no queda fantasma."""
    backend = make_backend(tmp_path)
    port = backend.approval()

    async def escenario():
        ctx = ApprovalContext(kind="command", task_id="t1", proposal_id="p1", summary="permiso")
        espera = asyncio.create_task(port.request({"proposal_id": "p1"}, ctx))
        turno = asyncio.create_task(backend.send("algo"))
        for _ in range(100):
            if backend._turnos and port.pending():
                break
            await asyncio.sleep(0.01)
        backend.cancel_turn()
        decision = await espera
        await turno
        return decision, backend.pending_approvals()

    decision, pendientes = asyncio.run(escenario())

    assert decision.status is ApprovalStatus.REJECTED
    assert "cancelado" in decision.reason
    assert pendientes == []
