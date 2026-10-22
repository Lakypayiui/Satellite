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
from satellite.desktop.backend import BackendError, DesktopBackend  # noqa: E402
from satellite.security.policy import DefaultSecurityPolicy  # noqa: E402
from satellite.tui.session import Session  # noqa: E402

TOKEN = "token-aprobaciones"


class FakeSession:
    """Sólo hace falta para que el backend exista; las aprobaciones no lo tocan."""

    def __init__(self, root, home=None) -> None:
        self._real = Session(root, home=home)

    def __getattr__(self, name):
        return getattr(self._real, name)


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


async def esperar_pendiente(
    backend: DesktopBackend, proposal_id: str, *, project_root=None
) -> None:
    """Espera a que el puerto de ese proyecto tenga la propuesta esperando decisión."""
    for _ in range(50):
        if backend.approval(project_root).is_pending(proposal_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"la propuesta {proposal_id!r} no llegó a estar pendiente")


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
    assert listado[0]["project_root"] == str(backend.project_root)


def test_options_for_other_kinds_have_no_session_grant(entorno) -> None:
    client, backend = entorno

    async def escenario():
        tarea = await pending_then(backend, kind="skill", proposal_id="s1")
        listado = client.get("/api/approvals").json()["pending"]
        backend.resolve_approval("s1", "reject")
        await tarea
        return listado[0]["options"]

    assert run(escenario()) == ["approve", "reject", "needs_changes"]


def test_a_failed_project_switch_keeps_the_previous_project(tmp_path: Path) -> None:
    """Si preparar el proyecto nuevo falla, el backend no puede quedarse apuntando a él."""

    class SessionQueFalla:
        def __init__(self, root, home=None) -> None:
            self.root = Path(root)

        def paths(self):
            if self.root == (tmp_path / "roto").resolve():
                raise OSError("sin permisos")
            return {}

    backend = DesktopBackend(
        tmp_path,
        token=TOKEN,
        home=tmp_path / "home",
        session_factory=SessionQueFalla,
        recents_path=tmp_path / "recents.json",
    )
    roto = tmp_path / "roto"
    roto.mkdir()

    with pytest.raises(BackendError) as excinfo:
        backend.open_project(roto)

    assert excinfo.value.code == "project_unavailable"
    assert backend.project_root == tmp_path.resolve()


def test_a_pending_approval_resolves_in_the_project_that_requested_it(entorno, tmp_path) -> None:
    """Cambiar de proyecto no puede redirigir la decisión al puerto del proyecto nuevo."""
    client, backend = entorno
    otro = tmp_path / "otro"
    otro.mkdir()

    async def escenario():
        tarea = asyncio.create_task(pedir(backend, proposal_id="p1"))
        await esperar_pendiente(backend, "p1", project_root=tmp_path)
        backend.open_project(otro)
        respuesta = client.post(
            "/api/approvals/p1",
            json={"decision": "reject", "project_root": str(tmp_path)},
        )
        return respuesta, await tarea

    respuesta, decision = run(escenario())

    assert respuesta.status_code == 200
    assert respuesta.json()["project_root"] == str(tmp_path.resolve())
    assert decision.status is ApprovalStatus.REJECTED


def test_an_ambiguous_proposal_id_is_not_resolved_in_the_wrong_project(
    entorno, tmp_path
) -> None:
    """Con el mismo id pendiente en dos proyectos, sin proyecto explícito no se adivina."""
    client, backend = entorno
    otro = tmp_path / "otro"
    otro.mkdir()

    async def escenario():
        tarea_a = asyncio.create_task(pedir(backend, proposal_id="p1"))
        await esperar_pendiente(backend, "p1", project_root=tmp_path)
        backend.open_project(otro)
        tarea_b = asyncio.create_task(pedir(backend, proposal_id="p1"))
        await esperar_pendiente(backend, "p1", project_root=otro)

        sin_proyecto = client.post("/api/approvals/p1", json={"decision": "approve"})
        sigue_a = backend.approval(tmp_path).is_pending("p1")
        sigue_b = backend.approval(otro).is_pending("p1")

        con_proyecto = client.post(
            "/api/approvals/p1",
            json={"decision": "reject", "project_root": str(otro)},
        )
        decision_b = await tarea_b
        sigue_a_despues = backend.approval(tmp_path).is_pending("p1")

        client.post(
            "/api/approvals/p1",
            json={"decision": "reject", "project_root": str(tmp_path)},
        )
        await tarea_a
        return sin_proyecto, sigue_a, sigue_b, con_proyecto, decision_b, sigue_a_despues

    sin_proyecto, sigue_a, sigue_b, con_proyecto, decision_b, sigue_a_despues = run(
        escenario()
    )

    assert sin_proyecto.status_code == 409
    assert sin_proyecto.json()["error"]["code"] == "ambiguous_approval"
    assert sigue_a is True
    assert sigue_b is True
    assert con_proyecto.status_code == 200
    assert decision_b.status is ApprovalStatus.REJECTED
    assert sigue_a_despues is True


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
