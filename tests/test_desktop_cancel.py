"""Cancelación del turno en la app: cooperativa cuando el runtime cede, y si no, corte."""

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
    """Un turno que no termina solo: sólo lo corta la cancelación dura."""

    def __init__(self, root, home=None) -> None:
        self.root = Path(root)
        self.bus = EventBus()
        self.persistence = RecordingStore()

    def runtime(self, *, force: bool = False):
        return (None, self.bus, self.persistence, None)

    async def send_message(self, text, *, approval=None, plan_mode=False, extra_metadata=None):
        await asyncio.Event().wait()  # nunca se pone
        raise AssertionError("no debería llegar aquí")


class OrchestratorWithCancel:
    """Orquestador que acepta la cancelación cooperativa."""

    def __init__(self) -> None:
        self.pedidos: list[str] = []

    def cancel(self, task_id: str = "", reason: str = "") -> dict:
        self.pedidos.append(reason)
        return {"cancelled": True, "task_id": task_id, "reason": reason or "cancelado"}


class CooperativeSession(SlowSession):
    """Turno que cede a la petición y termina, como el runtime de verdad entre nodos."""

    def __init__(self, root, home=None) -> None:
        super().__init__(root, home)
        self.orchestrator = OrchestratorWithCancel()

    def runtime(self, *, force: bool = False):
        return (self.orchestrator, self.bus, self.persistence, None)

    async def send_message(self, text, *, approval=None, plan_mode=False, extra_metadata=None):
        await asyncio.sleep(0.05)
        return {"text": "", "task_id": "t1", "status": "cancelled", "cancelled": True}


def make_backend(tmp_path: Path, factory=SlowSession) -> DesktopBackend:
    return DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=factory,
        recents_path=tmp_path / "recents.json",
    )


async def esperar_turno(backend: DesktopBackend) -> None:
    for _ in range(100):
        if backend._turnos:
            return
        await asyncio.sleep(0.01)


def test_the_cooperative_path_is_preferred_and_said(tmp_path: Path) -> None:
    backend = make_backend(tmp_path, CooperativeSession)

    async def escenario():
        turno = asyncio.create_task(backend.send("haz algo largo"))
        await esperar_turno(backend)
        pedido = await backend.cancel_turn()
        await turno
        return pedido, backend.session().orchestrator.pedidos

    pedido, pedidos = asyncio.run(escenario())

    assert pedido["cancelled"] is True
    assert pedido["how"] == "cooperative"
    assert "paró entre nodos" in pedido["note"]
    assert pedidos and "app" in pedidos[0]


def test_without_runtime_support_it_cuts_the_task_and_says_so(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    async def escenario():
        turno = asyncio.create_task(backend.send("haz algo largo"))
        await esperar_turno(backend)
        pedido = await backend.cancel_turn()
        return pedido, await turno

    pedido, respuesta = asyncio.run(escenario())

    assert pedido["cancelled"] is True
    assert pedido["how"] == "hard"
    assert "no cedió" in pedido["note"]
    assert respuesta["cancelled"] is True
    assert respuesta["status"] == "cancelled"
    assert backend._turnos == {}  # no queda un turno colgando


def test_cancelling_without_a_turn_says_so(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    with pytest.raises(BackendError) as fallo:
        asyncio.run(backend.cancel_turn())

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
        await backend.cancel_turn()
        decision = await espera
        await turno
        return decision, backend.pending_approvals()

    decision, pendientes = asyncio.run(escenario())

    assert decision.status is ApprovalStatus.REJECTED
    assert "cancelado" in decision.reason
    assert pendientes == []
