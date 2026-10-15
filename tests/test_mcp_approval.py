"""Elicitación MCP: se pregunta al cliente y, sin veredicto, se rechaza."""

import asyncio
import io
import json

from satellite.approval.base import ApprovalContext
from satellite.approval.mcp import (
    APPROVAL_SCHEMA,
    UNAVAILABLE_REASON,
    McpApproval,
    approval_message,
)
from satellite.core.proposal import ApprovalStatus
from satellite.mcp.server import PROTOCOL_BASE, PROTOCOL_WITH_ELICITATION, MCPServer


def run(coro):
    return asyncio.run(coro)


def make_ctx(**extra) -> ApprovalContext:
    base = {"kind": "agent", "task_id": "t1", "summary": "crear agente 'resumidor'"}
    base.update(extra)
    return ApprovalContext(**base)


def make_port(action: dict, **kwargs):
    """Puerto con un canal falso que devuelve esa acción."""
    calls: list = []

    def channel(message, schema, timeout):
        calls.append({"message": message, "schema": schema, "timeout": timeout})
        return dict(action)

    return McpApproval(channel, **kwargs), calls


# ---------------------------------------------------------------- decisiones


def test_client_accept_approves() -> None:
    port, calls = make_port({"action": "accept", "content": {"approve": True}})
    decision = run(port.request({"proposal_id": "prop1"}, make_ctx()))
    assert decision.status is ApprovalStatus.APPROVED
    assert calls and "prop1" in calls[0]["message"]


def test_accept_without_approval_rejects() -> None:
    port, _ = make_port({"action": "accept", "content": {"approve": False}})
    decision = run(port.request({"proposal_id": "prop1"}, make_ctx()))
    assert decision.status is ApprovalStatus.REJECTED


def test_decline_cancel_and_timeout_all_reject() -> None:
    for action in ({"action": "decline"}, {"action": "cancel"}, {"action": "timeout"}):
        port, _ = make_port(action)
        decision = run(port.request({"proposal_id": "prop1"}, make_ctx()))
        assert decision.status is ApprovalStatus.REJECTED, action


def test_without_a_client_channel_it_rejects_with_the_usual_message() -> None:
    port = McpApproval(None)
    decision = run(port.request({"proposal_id": "prop1"}, make_ctx()))
    assert decision.status is ApprovalStatus.REJECTED
    assert UNAVAILABLE_REASON in decision.reason


def test_a_channel_that_explodes_still_rejects() -> None:
    async def boom(*_args, **_kwargs):
        raise RuntimeError("cliente roto")

    port = McpApproval(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")), timeout_s=0.1)
    decision = run(port.request({"proposal_id": "prop1"}, make_ctx()))
    assert decision.status is ApprovalStatus.REJECTED


# ---------------------------------------------------------------- riesgo


def test_high_risk_proposals_are_never_asked() -> None:
    for details in (
        {"network": True, "capabilities": "http"},
        {"tools": ["run_shell", "read_file"]},
        {"write_files": True},
    ):
        port, calls = make_port({"action": "accept", "content": {"approve": True}})
        decision = run(port.request({"proposal_id": "prop1"}, make_ctx(details=details)))
        assert decision.status is ApprovalStatus.REJECTED, details
        assert "riesgo" in decision.reason
        assert calls == []  # ni se pregunta


def test_edit_and_delegation_are_not_approvable_by_elicitation() -> None:
    for kind in ("edit", "delegation"):
        port, calls = make_port({"action": "accept", "content": {"approve": True}})
        decision = run(port.request({"proposal_id": "prop1"}, make_ctx(kind=kind)))
        assert decision.status is ApprovalStatus.REJECTED
        assert calls == []


# ---------------------------------------------------------------- mensaje


def test_message_is_structured_and_carries_no_secrets() -> None:
    message = approval_message(
        "prop1",
        "crear agente 'x'",
        {"capabilities": "summarize", "api_key": "nvapi-secreto", "token": "t0ken"},
    )
    assert "prop1" in message
    assert "capabilities=summarize" in message
    assert "nvapi-secreto" not in message
    assert "t0ken" not in message
    assert "approve" in message


def test_message_truncates_long_summaries() -> None:
    message = approval_message("prop1", "x" * 5000, None)
    assert len(message) < 1000


def test_schema_is_a_flat_object_of_primitives() -> None:
    assert APPROVAL_SCHEMA["type"] == "object"
    assert APPROVAL_SCHEMA["properties"]["approve"]["type"] == "boolean"
    assert APPROVAL_SCHEMA["required"] == ["approve"]


def test_pending_is_reported() -> None:
    port, _ = make_port({"action": "decline"})
    run(port.request({"proposal_id": "prop1"}, make_ctx()))
    assert port.pending()[0]["proposal_id"] == "prop1"
    assert port.pending()[0]["kind"] == "agent"


# ---------------------------------------------------------------- servidor


def make_server(stdin_text: str = "", *, capabilities: dict | None = None):
    stdin = io.StringIO(stdin_text)
    stdout = io.StringIO()
    server = MCPServer(bundle=object(), stdin=stdin, stdout=stdout)  # type: ignore[arg-type]
    if capabilities is not None:
        server.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": capabilities}}
        )
    return server, stdin, stdout


def test_initialize_reads_the_client_capability_and_negotiates() -> None:
    server, _, _ = make_server(capabilities={"elicitation": {}})
    assert server.client_elicitation is True
    response = server.dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"capabilities": {"elicitation": {}}}}
    )
    assert response["result"]["protocolVersion"] == PROTOCOL_WITH_ELICITATION
    # el servidor no declara elicitation: solo tools
    assert "elicitation" not in response["result"]["capabilities"]

    plain, _, _ = make_server(capabilities={})
    assert plain.client_elicitation is False
    assert (
        plain.dispatch({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}})[
            "result"
        ]["protocolVersion"]
        == PROTOCOL_BASE
    )


def test_request_elicitation_without_capability_is_unavailable() -> None:
    server, _, _ = make_server()
    assert server.request_elicitation("m", {"type": "object"}) == {"action": "unavailable"}


def test_request_elicitation_returns_the_reply_and_queues_the_rest() -> None:
    reply = json.dumps(
        {"jsonrpc": "2.0", "id": "elicitation-1", "result": {"action": "accept", "content": {"approve": True}}}
    )
    other = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    server, _, stdout = make_server(
        f"{other}\n{reply}\n", capabilities={"elicitation": {}}
    )
    result = server.request_elicitation("¿apruebas?", {"type": "object"}, timeout_s=2)
    assert result["action"] == "accept"
    # el mensaje ajeno no se pierde: queda encolado para el bucle principal
    assert [m.get("id") for m in server._queued] == [9]
    assert "elicitation/create" in stdout.getvalue()


def test_request_elicitation_times_out_without_hanging() -> None:
    server, _, _ = make_server("", capabilities={"elicitation": {}})
    assert server.request_elicitation("m", {"type": "object"}, timeout_s=0.2) == {
        "action": "timeout"
    }
