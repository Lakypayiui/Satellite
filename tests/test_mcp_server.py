"""Tests del MCPServer (stdio simulado, sin red)."""

import io
import json

from satellite.mcp.server import MCPServer


def make_server():
    """Server with fake stdin/stdout and a stub bundle (no runtime needed)."""
    stdin = io.StringIO()
    stdout = io.StringIO()
    server = MCPServer(bundle=object(), stdin=stdin, stdout=stdout)  # type: ignore[arg-type]
    return server, stdin, stdout


def test_initialize_returns_server_info() -> None:
    server, _, stdout = make_server()
    resp = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "satellite"
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_tools_list_contains_tools() -> None:
    server, _, _ = make_server()
    resp = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "satellite_run" in names
    assert "satellite_agents" in names
    assert "satellite_status" in names


def test_tools_call_agents_with_fake_bundle(monkeypatch) -> None:
    """satellite_agents con bundle fake que devuelve un catálogo."""
    from satellite.core.spec import AgentSpec

    class FakeRegistry:
        def all(self):
            return [AgentSpec(id="html_writer", name="W", capabilities=("write_html",), prompt_ref="p")]

    class FakeBundle:
        def get(self, root):
            return {"registry": FakeRegistry(), "orchestrator": None, "store": None}

    server = MCPServer(bundle=FakeBundle())  # type: ignore[arg-type]
    resp = server.dispatch({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "satellite_agents", "arguments": {}},
    })
    assert resp["id"] == 3
    assert resp["result"]["isError"] is False
    assert "html_writer" in resp["result"]["content"][0]["text"]


def test_tools_call_run_executes_with_fake_orchestrator(monkeypatch) -> None:
    from satellite.core.task import TaskResult, TaskStatus

    class FakeOrch:
        async def execute(self, task):
            return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="tarea completa")

    class FakeStore:
        pass

    class FakeRegistry:
        def all(self):
            return []

    class FakeBundle:
        def __init__(self):
            self._active = {}

        def get(self, root):
            return {"orchestrator": FakeOrch(), "registry": FakeRegistry(), "store": FakeStore()}

    server = MCPServer(bundle=FakeBundle())  # type: ignore[arg-type]
    resp = server.dispatch({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "satellite_run", "arguments": {"goal": "crear html"}},
    })
    assert resp["id"] == 4
    assert resp["result"]["isError"] is False
    assert "tarea completa" in resp["result"]["content"][0]["text"]


def test_tools_list_exposes_no_approving_tool() -> None:
    """MCP puede consultar, pero no aprobar (nada de autoaprobación)."""
    server, _, _ = make_server()
    resp = server.dispatch({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "satellite_approve" not in names
    assert "satellite_approvals" in names
    assert all("approve" not in name for name in names)


def test_approve_tool_is_not_callable() -> None:
    server, _, _ = make_server()
    resp = server.dispatch({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "satellite_approve",
            "arguments": {"proposal_id": "prop1", "status": "approved"},
        },
    })
    assert resp["error"]["code"] == -32601


def test_mcp_approval_never_approves_and_never_blocks() -> None:
    """Sin humano delante: rechaza con motivo claro y deja la propuesta a la vista."""
    import asyncio

    from satellite.approval.base import ApprovalContext
    from satellite.approval.mcp import DENIED_REASON, McpApproval
    from satellite.core.proposal import ApprovalStatus

    port = McpApproval()
    ctx = ApprovalContext(kind="agent", task_id="t1", summary="crear agente x")
    # antes este `await` se quedaba colgado hasta que alguien aprobara por MCP
    decision = asyncio.run(
        asyncio.wait_for(port.request({"proposal_id": "prop1", "name": "x"}, ctx), timeout=2)
    )
    assert decision.status is ApprovalStatus.REJECTED
    assert DENIED_REASON in decision.reason
    # Rechazada: la ejecución siguió, así que no queda «pendiente».
    assert port.pending() == []

    # y la tool de lectura lo dice así (no lista decisiones ya tomadas)
    class FakeBundleWithApproval:
        approval = port

        def get(self, root):
            return {"registry": None, "orchestrator": None, "store": None}

    server = MCPServer(bundle=FakeBundleWithApproval())  # type: ignore[arg-type]
    resp = server.dispatch({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "satellite_approvals", "arguments": {"task_id": "t1"}},
    })
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    assert "sin propuestas pendientes" in text


def test_unknown_method_returns_error() -> None:
    server, _, _ = make_server()
    resp = server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "tools/bogus"})
    assert resp["error"]["code"] == -32601


def test_serve_reads_lines_and_responds() -> None:
    stdin = io.StringIO()
    stdout = io.StringIO()
    server = MCPServer(bundle=object(), stdin=stdin, stdout=stdout)  # type: ignore[arg-type]
    stdin.write(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}) + "\n")
    stdin.seek(0)
    server.serve()
    lines = stdout.getvalue().strip().splitlines()
    assert len(lines) == 1
    resp = json.loads(lines[0])
    assert resp["id"] == 9
