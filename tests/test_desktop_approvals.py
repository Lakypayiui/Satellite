"""Aprobaciones por API: una vez, durante la sesión, o rechazo. Y quién puede decidir."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.approval.base import ApprovalContext  # noqa: E402
from satellite.core.proposal import ApprovalStatus  # noqa: E402
from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.approvals import decision_from  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402
from satellite.security.policy import DefaultSecurityPolicy  # noqa: E402
from satellite.tui.session import Session  # noqa: E402

TOKEN = "token-aprobaciones"


class FakeSession:
    """Sólo hace falta para que el backend exista; las aprobaciones no lo tocan."""

    def __init__(self, root, home=None) -> None:
        self._real = Session(root, home=home)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def entorno(tmp_path: Path):
    backend = DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=FakeSession,
        recents_path=tmp_path / "recents.json",
    )
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return client, backend


async def pedir(backend: DesktopBackend, *, kind: str = "command", proposal_id: str = "p1"):
    """Deja una propuesta esperando decisión, como haría el agente."""
    ctx = ApprovalContext(
        kind=kind,
        task_id="t1",
        proposal_id=proposal_id,
        summary=f"permiso para {proposal_id}",
        details={"command": "git status", "consequence": "ejecución"},
    )
    return await backend.approval().request({"proposal_id": proposal_id}, ctx)


async def pending_then(backend: DesktopBackend, *, kind: str = "command", proposal_id: str = "p1"):
    """Deja la petición esperando y devuelve la tarea, ya en la lista de pendientes."""
    tarea = asyncio.create_task(pedir(backend, kind=kind, proposal_id=proposal_id))
    for _ in range(50):
        if backend.pending_approvals():
            break
        await asyncio.sleep(0.01)
    return tarea


# --- lo que ve la interfaz ---


def test_a_pending_permission_shows_its_reason_and_options(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend)
        listado = client.get("/api/approvals").json()["pending"]
        backend.resolve_approval("p1", "reject")
        await tarea
        return listado

    listado = run(escenario())

    assert len(listado) == 1
    assert listado[0]["proposal_id"] == "p1"
    assert listado[0]["summary"] == "permiso para p1"
    assert listado[0]["reason"] == "ejecución"
    assert listado[0]["options"] == ["once", "session", "reject"]


def test_options_for_other_kinds_have_no_session_grant(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend, kind="skill", proposal_id="s1")
        listado = client.get("/api/approvals").json()["pending"]
        backend.resolve_approval("s1", "reject")
        await tarea
        return listado[0]["options"]

    assert run(escenario()) == ["approve", "reject", "needs_changes"]


# --- las tres decisiones ---


def test_approving_once_grants_only_this_time(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend)
        respuesta = client.post("/api/approvals/p1", json={"decision": "approve", "feedback": "once"})
        decision = await tarea
        return respuesta, decision

    respuesta, decision = run(escenario())

    assert respuesta.status_code == 200
    assert decision.status is ApprovalStatus.APPROVED
    assert decision.feedback == "once"
    assert backend.pending_approvals() == []


def test_approving_for_the_session_is_said_explicitly(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend)
        client.post("/api/approvals/p1", json={"decision": "approve", "feedback": "session"})
        return await tarea

    assert run(escenario()).feedback == "session"


def test_approving_without_saying_feedback_still_grants_once(entorno) -> None:
    _client, backend = entorno

    assert decision_from("approve").feedback == "once"


def test_rejecting_keeps_the_reason_and_grants_nothing(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend)
        client.post("/api/approvals/p1", json={"decision": "reject", "reason": "no lo necesito"})
        return await tarea

    decision = run(escenario())

    assert decision.status is ApprovalStatus.REJECTED
    assert decision.feedback == ""
    assert decision.reason == "no lo necesito"


# --- lo que no se puede hacer ---


def test_nothing_can_be_approved_that_is_not_pending(entorno) -> None:
    """La interfaz sólo decide sobre propuestas que el runtime ha pedido de verdad."""
    client, _backend = entorno

    respuesta = client.post("/api/approvals/inventada", json={"decision": "approve"})

    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["code"] == "not_pending"


def test_an_unknown_decision_changes_nothing(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend)
        respuesta = client.post("/api/approvals/p1", json={"decision": "vale-pero-no"})
        siguen = len(backend.pending_approvals())
        client.post("/api/approvals/p1", json={"decision": "reject"})
        await tarea
        return respuesta, siguen

    respuesta, siguen = run(escenario())

    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "invalid_decision"
    assert siguen == 1  # la propuesta sigue pendiente, intacta


def test_a_bad_feedback_value_is_rejected(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend)
        respuesta = client.post("/api/approvals/p1", json={"decision": "approve", "feedback": "siempre"})
        client.post("/api/approvals/p1", json={"decision": "reject"})
        await tarea
        return respuesta

    assert run(escenario()).status_code == 422


# --- permisos de comandos (lo que no se relaja) ---


def test_the_command_veto_still_wins_over_any_allowlist() -> None:
    """``allow_terminal=False`` es veto duro: sin terminal no hay allowlist que valga."""
    policy = DefaultSecurityPolicy(
        allowed_tools={"run_command"}, allow_terminal=False, allowed_commands=("git",)
    )

    assert policy.check_command(["git", "status"]) is False


def test_with_terminal_enabled_and_an_empty_allowlist_everything_is_asked() -> None:
    """Denegar por defecto y poder preguntar: el camino que usa la app."""
    policy = DefaultSecurityPolicy(allowed_tools={"run_command"}, allow_terminal=True)

    assert policy.check_command(["git", "status"]) is False
    assert policy.check_command(["python", "-c", "1"]) is False


def test_an_approval_only_grants_the_binary_it_says() -> None:
    """Aprobar el binario no abre la mano a ningún otro."""
    policy = DefaultSecurityPolicy(allowed_tools={"run_command"}, allow_terminal=True)

    policy.allowed_commands = ("git",)

    assert policy.check_command(["git", "status"]) is True
    assert policy.check_command(["curl", "http://ejemplo"]) is False
    assert policy.check_command(["rm", "-rf", "/"]) is False
