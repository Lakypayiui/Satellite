"""Fase C del plan de correcciones (docs/PLAN-correcciones.md), punto 5.

Cancelar por MCP: la ejecución se registra antes de empezar, la cancelación llega
mientras corre (el servidor atiende peticiones a la vez) y nunca lanza excepción.
"""

from __future__ import annotations

import asyncio
import io
import json

from satellite.core.task import TaskResult, TaskStatus
from satellite.mcp.handlers import get_handlers
from satellite.mcp.server import MCPServer


class _SlowOrchestrator:
    """Espera a que le pidan cancelar (o a un tope) y devuelve el estado."""

    def __init__(self) -> None:
        self._cancel: asyncio.Event | None = None
        self.started = 0

    def _event(self) -> asyncio.Event:
        if self._cancel is None:
            self._cancel = asyncio.Event()
        return self._cancel

    async def execute(self, task):
        self.started += 1
        try:
            await asyncio.wait_for(self._event().wait(), timeout=3)
        except asyncio.TimeoutError:
            return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="terminó")
        return TaskResult(task_id=task.task_id, status=TaskStatus.CANCELLED, summary="cancelada")

    def cancel(self, task_id: str = "", reason: str = "") -> dict:
        self._event().set()
        return {"cancelled": True, "task_id": task_id, "reason": reason}


class _Bundle:
    def __init__(self) -> None:
        self._active: dict = {}
        self.orchestrator = _SlowOrchestrator()
        self.roots: list = []

    def get(self, root):
        self.roots.append(root)
        return {"orchestrator": self.orchestrator, "registry": None, "store": None}


def test_a_run_can_be_cancelled_while_it_is_running(tmp_path) -> None:
    bundle = _Bundle()
    handlers = get_handlers(bundle)  # type: ignore[arg-type]

    async def scenario():
        running = asyncio.ensure_future(
            handlers["satellite_run"](
                {"goal": "algo largo", "project_root": str(tmp_path), "task_id": "t-largo"}
            )
        )
        await asyncio.sleep(0.05)
        cancelled = await handlers["satellite_cancel"]({"task_id": "t-largo"})
        return cancelled, await running

    cancelled, result = asyncio.run(scenario())

    assert "cancelación pedida" in cancelled["text"]
    assert "status: cancelled" in result["text"]
    assert "t-largo" in result["text"]
    assert bundle._active == {}  # se limpia al terminar
    # Lo que se guarda y se usa es la ruta, no el runtime.
    assert all(isinstance(root, str) for root in bundle.roots)


def test_cancel_without_task_id_stops_the_active_runs(tmp_path) -> None:
    bundle = _Bundle()
    handlers = get_handlers(bundle)  # type: ignore[arg-type]

    async def scenario():
        running = asyncio.ensure_future(
            handlers["satellite_run"]({"goal": "x", "project_root": str(tmp_path)})
        )
        await asyncio.sleep(0.05)
        cancelled = await handlers["satellite_cancel"]({})
        return cancelled, await running

    cancelled, result = asyncio.run(scenario())

    assert "cancelación pedida" in cancelled["text"]
    assert "status: cancelled" in result["text"]


def test_cancelling_a_finished_or_unknown_task_never_raises(tmp_path) -> None:
    bundle = _Bundle()
    bundle.orchestrator.cancel()  # la ejecución termina en cuanto empieza
    handlers = get_handlers(bundle)  # type: ignore[arg-type]

    async def scenario():
        await handlers["satellite_run"](
            {"goal": "x", "project_root": str(tmp_path), "task_id": "t-hecha"}
        )
        after = await handlers["satellite_cancel"]({"task_id": "t-hecha"})
        unknown = await handlers["satellite_cancel"]({"task_id": "no-existe"})
        return after, unknown

    after, unknown = asyncio.run(scenario())

    assert "no hay ejecución que cancelar" in after["text"]
    assert "no hay ejecución que cancelar" in unknown["text"]


def test_the_server_answers_a_cancel_while_a_run_is_in_flight(tmp_path) -> None:
    bundle = _Bundle()
    stdin = io.StringIO()
    stdout = io.StringIO()
    server = MCPServer(bundle=bundle, stdin=stdin, stdout=stdout)  # type: ignore[arg-type]
    run_call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "satellite_run",
            "arguments": {"goal": "largo", "project_root": str(tmp_path), "task_id": "t1"},
        },
    }
    cancel_call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "satellite_cancel", "arguments": {"task_id": "t1"}},
    }
    stdin.write(json.dumps(run_call) + "\n" + json.dumps(cancel_call) + "\n")
    stdin.seek(0)

    server.serve()

    responses = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    by_id = {response["id"]: response for response in responses}
    # La cancelación se responde antes de que acabe la ejecución, y la detiene.
    assert [response["id"] for response in responses] == [2, 1]
    assert "cancelación pedida" in by_id[2]["result"]["content"][0]["text"]
    assert "status: cancelled" in by_id[1]["result"]["content"][0]["text"]


def test_an_elicitation_during_serve_gets_its_reply_without_a_second_reader() -> None:
    import os
    import threading
    import time

    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "r")
    client = os.fdopen(write_fd, "w")
    stdout = io.StringIO()
    server = MCPServer(bundle=object(), stdin=stdin, stdout=stdout)  # type: ignore[arg-type]
    server.client_elicitation = True
    answers: list = []

    async def asks(_arguments):
        answer = await asyncio.to_thread(server.request_elicitation, "¿sí?", {}, 3)
        answers.append(answer)
        return {"text": str(answer.get("action"))}

    server.handlers["pregunta"] = asks

    def fake_client() -> None:
        client.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "pregunta", "arguments": {}},
        }) + "\n")
        client.flush()
        deadline = time.monotonic() + 3
        while "elicitation/create" not in stdout.getvalue() and time.monotonic() < deadline:
            time.sleep(0.01)
        client.write(json.dumps({
            "jsonrpc": "2.0", "id": "elicitation-1",
            "result": {"action": "accept", "content": {"approve": True}},
        }) + "\n")
        client.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        client.flush()
        time.sleep(0.2)
        client.close()

    threading.Thread(target=fake_client, daemon=True).start()
    server.serve()

    assert answers == [{"action": "accept", "content": {"approve": True}}]
    responses = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    ids = [response.get("id") for response in responses]
    # La petición de elicitación, la respuesta de la tool y la de tools/list: nada se
    # pierde en un lector duplicado.
    assert "elicitation-1" in ids and 1 in ids and 2 in ids
