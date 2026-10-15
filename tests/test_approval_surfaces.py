"""Tests de las superficies de aprobación: Web (FastAPI) y MCP (Fase 4)."""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from satellite.approval.base import ApprovalContext, AutoApprovalPort
from satellite.approval.mcp import McpApproval
from satellite.approval.web import WebApproval
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.events.bus import EventBus
from satellite.mcp.handlers import get_handlers
from satellite.web.app import create_app


def run(coro):
    return asyncio.run(coro)


def ctx() -> ApprovalContext:
    return ApprovalContext(kind="agent", proposal_id="p1", summary="s")


class _FakeStore:
    def list_tasks(self):
        return []


class _FakeRegistry:
    def all(self):
        return []


class _FakeOrch:
    async def execute(self, task):
        raise AssertionError("no debe ejecutarse en este test")


def _runtime() -> tuple:
    return (_FakeOrch(), EventBus(), _FakeStore(), _FakeRegistry())


def test_web_port_submit_resolves() -> None:
    async def scenario():
        port = WebApproval()
        task = asyncio.create_task(port.request({}, ctx()))
        await asyncio.sleep(0)
        port.submit("p1", ApprovalDecision(status=ApprovalStatus.APPROVED))
        return await task

    assert run(scenario()).status is ApprovalStatus.APPROVED


def test_mcp_port_submit_resolves() -> None:
    async def scenario():
        port = McpApproval()
        task = asyncio.create_task(port.request({}, ctx()))
        await asyncio.sleep(0)
        port.submit("p1", ApprovalDecision(status=ApprovalStatus.NEEDS_CHANGES, feedback="f"))
        return await task

    assert run(scenario()).status is ApprovalStatus.NEEDS_CHANGES


def test_web_endpoint_requires_submittable_port(tmp_path) -> None:
    app = create_app(tmp_path, runtime=_runtime(), approval=AutoApprovalPort())
    client = TestClient(app)
    response = client.post("/api/run/x/approve", json={"status": "approved"})
    assert response.status_code == 400


def test_web_endpoint_invalid_status(tmp_path) -> None:
    app = create_app(tmp_path, runtime=_runtime())
    client = TestClient(app)
    response = client.post("/api/run/x/approve", json={"status": "maybe"})
    assert response.status_code == 400


def test_web_endpoint_accepts_decision(tmp_path) -> None:
    app = create_app(tmp_path, runtime=_runtime())
    client = TestClient(app)
    response = client.post(
        "/api/run/x/approve",
        json={"proposal_id": "p9", "status": "approved", "reason": "ok"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status"] == "approved"


def test_web_approvals_endpoint_lists_pending(tmp_path) -> None:
    app = create_app(tmp_path, runtime=_runtime())
    client = TestClient(app)
    assert client.get("/api/approvals").json() == {"pending": []}


def test_mcp_approve_handler_resolves_parked_request() -> None:
    async def scenario():
        port = McpApproval()
        bundle = SimpleNamespace(approval=port)
        handlers = get_handlers(bundle)
        task = asyncio.create_task(port.request({}, ctx()))
        await asyncio.sleep(0)
        result = await handlers["satellite_approve"]({"proposal_id": "p1", "status": "approved"})
        decision = await task
        return result, decision

    result, decision = run(scenario())
    assert result["isError"] is False
    assert decision.status is ApprovalStatus.APPROVED


def test_mcp_approve_handler_invalid_status() -> None:
    bundle = SimpleNamespace(approval=McpApproval())
    handlers = get_handlers(bundle)
    result = run(handlers["satellite_approve"]({"proposal_id": "p1", "status": "maybe"}))
    assert result["isError"] is True
