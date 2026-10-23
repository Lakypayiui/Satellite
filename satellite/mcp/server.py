"""MCPServer: JSON-RPC 2.0 over stdio, dispatching MCP tools.

``serve()`` atiende las peticiones a la vez: las tools corren en un bucle de eventos
persistente y un único lector de stdin sigue despachando mientras tanto, así una
``satellite_cancel`` llega mientras corre una ``satellite_run`` y las respuestas de
elicitación llegan a quien las espera.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import queue
import sys
import threading
import time
from typing import Any

from satellite.mcp.handlers import TOOLS_LIST, RuntimeBundle, get_handlers

# Versión base de este servidor y la primera que incluye elicitación (client-side).
PROTOCOL_BASE = "2024-11-05"
PROTOCOL_WITH_ELICITATION = "2025-06-18"
# Al perder al cliente, cuánto se espera a que las ejecuciones canceladas terminen.
EOF_DRAIN_TIMEOUT_S = 60.0


class MCPServer:
    """Minimal MCP server (stdio JSON-RPC 2.0) with zero third-party deps."""

    def __init__(
        self,
        bundle: RuntimeBundle | None = None,
        stdin: Any = None,
        stdout: Any = None,
    ) -> None:
        self.bundle = bundle or RuntimeBundle()
        self.handlers = get_handlers(self.bundle)
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._initialized = False
        # La elicitación es una capacidad del **cliente**: se lee, no se declara.
        self.client_elicitation = False
        self._elicitation_seq = 0
        self._queued: list[dict] = []
        # Con ``serve()`` en marcha: bucle de las tools, respuestas de elicitación
        # esperadas (id -> cola) y un cerrojo para no mezclar líneas en stdout.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._replies: dict[str, "queue.Queue[dict]"] = {}
        self._write_lock = threading.Lock()
        # El puerto de aprobación pregunta por este canal (y si no hay, rechaza).
        approval = getattr(self.bundle, "approval", None)
        if approval is not None:
            try:
                approval.elicitation = self.request_elicitation
            except AttributeError:  # puerto sin canal: sigue fail-closed
                pass

    def _send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with self._write_lock:
            self._stdout.write(line)
            self._stdout.flush()

    def dispatch(self, message: dict) -> dict | None:
        """Dispatch one JSON-RPC message; returns a response dict or None."""
        method = message.get("method")
        msg_id = message.get("id")

        if method == "initialize":
            params = message.get("params")
            capabilities = params.get("capabilities") if isinstance(params, dict) else None
            self.client_elicitation = bool(
                isinstance(capabilities, dict) and "elicitation" in capabilities
            )
            self._initialized = True
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    # Se ofrece la versión que soporta elicitación solo si el
                    # cliente la declara; si no, la base de siempre.
                    "protocolVersion": (
                        PROTOCOL_WITH_ELICITATION
                        if self.client_elicitation
                        else PROTOCOL_BASE
                    ),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "satellite", "version": "0.2.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS_LIST},
            }
        if method == "tools/call":
            params = message.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            handler = self.handlers.get(name)
            if handler is None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {name}"},
                }
            return self._call_tool(msg_id, handler, arguments)
        if method and method.startswith("tools/"):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": msg_id if msg_id is not None else None,
            "error": {"code": -32600, "message": "invalid request"},
        }

    def _call_tool(self, msg_id, handler, arguments) -> dict:
        """Ejecuta la tool y espera (``dispatch`` síncrono; ``serve`` no pasa por aquí)."""
        try:
            return _tool_response(msg_id, asyncio.run(handler(arguments)))
        except Exception as exc:  # noqa: BLE001
            return _tool_error(msg_id, exc)

    async def _run_tool(self, msg_id, handler, arguments) -> None:
        """Ejecuta la tool en el bucle de ``serve`` y envía su respuesta al acabar."""
        try:
            response = _tool_response(msg_id, await handler(arguments))
        except Exception as exc:  # noqa: BLE001
            response = _tool_error(msg_id, exc)
        self._send(response)

    def _submit_tool(self, message: dict) -> concurrent.futures.Future | None:
        """Lanza una ``tools/call`` sin esperarla (``None`` si no es una tool conocida)."""
        if self._loop is None or message.get("method") != "tools/call":
            return None
        params = message.get("params", {}) or {}
        handler = self.handlers.get(params.get("name", ""))
        if handler is None:
            return None
        coro = self._run_tool(message.get("id"), handler, params.get("arguments", {}) or {})
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _route_reply(self, message: dict) -> bool:
        """Entrega la respuesta del cliente a la elicitación que la espera."""
        if "method" in message:
            return False
        waiting = self._replies.pop(str(message.get("id")), None)
        if waiting is None:
            return False
        waiting.put(message)
        return True

    def serve(self) -> None:
        """Read lines from stdin, respond on stdout; clean exit on EOF.

        Las tools se lanzan en un bucle persistente y no bloquean la lectura; al llegar
        EOF se espera a las que sigan en curso antes de salir.
        """
        loop = asyncio.new_event_loop()
        runner = threading.Thread(target=loop.run_forever, name="mcp-tools", daemon=True)
        runner.start()
        self._loop = loop
        inflight: set[concurrent.futures.Future] = set()
        try:
            while True:
                line = self._stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                    continue
                if not isinstance(message, dict) or self._route_reply(message):
                    continue
                if "method" not in message:
                    # Respuesta a algo que ya no espera nadie (elicitación vencida): se
                    # ignora; contestarla con un error sería responder a una respuesta.
                    continue
                future = self._submit_tool(message)
                if future is not None:
                    inflight.add(future)
                    future.add_done_callback(inflight.discard)
                    continue
                response = self.dispatch(message)
                if response is not None:
                    self._send(response)
        finally:
            # Sin cliente no llegará ninguna respuesta: quien espera una elicitación
            # deja de esperar (fail-closed) y las tools en curso terminan.
            for waiting in list(self._replies.values()):
                waiting.put({"__eof__": True})
            self._replies.clear()
            # Y lo que siga ejecutándose se cancela: sin cliente nadie leerá el resultado
            # y seguir gastaría tokens y tocaría archivos a ciegas.
            self._cancel_active_runs("cliente desconectado")
            concurrent.futures.wait(list(inflight), timeout=EOF_DRAIN_TIMEOUT_S)
            loop.call_soon_threadsafe(loop.stop)
            runner.join(timeout=5)
            loop.close()
            self._loop = None

    def _cancel_active_runs(self, reason: str) -> None:
        active = getattr(self.bundle, "_active", None)
        if not isinstance(active, dict):
            return
        for task_id, root in list(active.items()):
            try:
                orchestrator = self.bundle.get(root)["orchestrator"]
                cancel = getattr(orchestrator, "cancel", None)
                if callable(cancel):
                    cancel(task_id, reason)
            except Exception:  # noqa: BLE001 — cerrar nunca falla por una cancelación
                continue

    # --- elicitación (pregunta al cliente) ---

    def request_elicitation(
        self, message: str, requested_schema: dict, timeout_s: float = 120.0
    ) -> dict:
        """Envía ``elicitation/create`` y espera la respuesta del cliente.

        Se llama desde el handler (que corre en ``asyncio.to_thread``), así que
        mientras espera nadie más lee el stdin. Devuelve siempre un dict con
        ``action``: ``accept``/``decline``/``cancel``, o ``timeout``/``unavailable``
        cuando no hay veredicto (fail-closed).
        """
        if not self.client_elicitation:
            return {"action": "unavailable"}
        with self._write_lock:  # varias aprobaciones a la vez no comparten id
            self._elicitation_seq += 1
            request_id = f"elicitation-{self._elicitation_seq}"
        if self._loop is not None:
            # Con ``serve()`` en marcha la respuesta la entrega su lector: aquí sólo se
            # espera en una cola propia (sin abrir otro lector de stdin).
            waiting: "queue.Queue[dict]" = queue.Queue()
            self._replies[request_id] = waiting
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "elicitation/create",
                    "params": {"message": message, "requestedSchema": requested_schema},
                }
            )
            try:
                reply = waiting.get(timeout=max(0.0, float(timeout_s)))
            except queue.Empty:
                self._replies.pop(request_id, None)
                return {"action": "timeout"}
            if reply.get("__eof__"):
                return {"action": "timeout"}
            result = reply.get("result")
            return result if isinstance(result, dict) else {"action": "cancel"}
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "elicitation/create",
                "params": {"message": message, "requestedSchema": requested_schema},
            }
        )
        return self._wait_for_reply(request_id, timeout_s)

    def _wait_for_reply(self, request_id: str, timeout_s: float) -> dict:
        """Espera la respuesta con ese id; lo demás se **encola** para el bucle."""
        incoming: "queue.Queue[dict]" = queue.Queue()

        def reader() -> None:
            while True:
                line = self._stdin.readline()
                if not line:
                    incoming.put({"__eof__": True})
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                incoming.put(message)

        threading.Thread(target=reader, daemon=True).start()
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"action": "timeout"}
            try:
                message = incoming.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if message.get("__eof__"):
                return {"action": "timeout"}
            if str(message.get("id")) == str(request_id):
                result = message.get("result")
                return result if isinstance(result, dict) else {"action": "cancel"}
            # Otro mensaje del cliente: no se pierde, lo procesa el bucle principal.
            self._queued.append(message)

    def _drain_queue(self) -> None:
        """Procesa los mensajes que llegaron mientras se esperaba una elicitación."""
        pending = list(self._queued)
        self._queued.clear()
        for message in pending:
            if not isinstance(message, dict) or "method" not in message:
                continue  # respuestas sueltas: no hay nada que despachar
            response = self.dispatch(message)
            if response is not None:
                self._send(response)


def _tool_response(msg_id, result: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": [{"type": "text", "text": result.get("text", "")}],
            "isError": result.get("isError", False),
        },
    }


def _tool_error(msg_id, exc: BaseException) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": [{"type": "text", "text": f"runtime error: {exc}"}],
            "isError": True,
        },
    }
