"""MCP handlers: each tool calls the runtime (via the Etapa 7 builder).

Aprobaciones: MCP **no** aprueba nada (``McpApproval`` rechaza y deja la
propuesta a la vista). Un host podría inyectar la aprobación en el prompt, así
que la decisión se toma siempre con un humano delante: CLI o TUI.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from satellite.approval.mcp import McpApproval
from satellite.cli.runtime_builder import build_runtime
from satellite.core.task import Task

_DEFAULT_ROOT = os.environ.get("SATELLITE_MCP_PROJECT_ROOT")
# Un id elegido por el cliente (para poder cancelar luego) es corto y sin rarezas.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _resolve_root(project_root) -> str:
    return str(Path(project_root or _DEFAULT_ROOT or Path.cwd()).resolve())


class RuntimeBundle:
    """Cached runtime per project_root: builder, active orchestrators, bus, store."""

    def __init__(self) -> None:
        self._runtimes: dict[str, dict] = {}
        self._active: dict[str, str] = {}  # task_id -> project_root
        self.approval = McpApproval()

    def get(self, project_root: Path | str | None) -> dict:
        """Build or retrieve the runtime for a project root."""
        root = _resolve_root(project_root)
        if root not in self._runtimes:
            # Gate explícito: MCP NO registra tools de control de escritorio.
            # Las aprobaciones de MCP van por elicitación/pendientes, nunca por consola:
            # en un servidor stdio, stdin es el canal del protocolo.
            orchestrator, bus, store, registry = build_runtime(
                Path(root), allow_desktop=False, approval=self.approval
            )
            self._runtimes[root] = {
                "orchestrator": orchestrator,
                "bus": bus,
                "store": store,
                "registry": registry,
            }
        return self._runtimes[root]


def get_handlers(bundle: RuntimeBundle) -> dict[str, callable]:
    """Return the MCP tool handlers mapping name -> async callable(arguments)."""

    async def satellite_run(arguments: dict) -> dict:
        goal = arguments.get("goal", "")
        if not goal.strip():
            return {"isError": True, "text": "goal vacío"}
        root = _resolve_root(arguments.get("project_root"))
        runtime = bundle.get(root)
        # El cliente puede elegir el id para poder cancelar mientras corre.
        requested_id = str(arguments.get("task_id") or "").strip()
        if requested_id:
            if not _TASK_ID_RE.match(requested_id):
                return {"isError": True, "text": "task_id inválido (letras, números, _ . : -)"}
            if requested_id in bundle._active:
                return {"isError": True, "text": f"ya hay una ejecución con id {requested_id}"}
            list_tasks = getattr(runtime.get("store"), "list_tasks", None)
            if callable(list_tasks) and requested_id in list_tasks():
                return {
                    "isError": True,
                    "text": f"ya existe una tarea guardada con id {requested_id}; elige otro",
                }
            task = Task(goal=goal, project_root=Path(root), task_id=requested_id)
        else:
            task = Task(goal=goal, project_root=Path(root))
        # Un turno a la vez por proyecto: el runtime (y su token de cancelación) es uno
        # por proyecto, y dos turnos a la vez se pisarían.
        busy = next((tid for tid, r in bundle._active.items() if r == root), None)
        if busy is not None:
            return {
                "isError": True,
                "text": f"ya hay una ejecución en curso para este proyecto ({busy}); "
                "espera a que termine o cancélala con satellite_cancel",
            }
        # Se registra ANTES de ejecutar (para poder cancelarla en curso) y con la ruta,
        # no con el runtime.
        bundle._active[task.task_id] = root
        try:
            result = await runtime["orchestrator"].execute(task)
        finally:
            bundle._active.pop(task.task_id, None)
        text = (
            f"task_id: {task.task_id}\nstatus: {result.status.value}\n"
            f"summary: {result.summary}"
        )
        if result.error:
            text += f"\nerror: {result.error}"
        return {"isError": result.status.value == "failed", "text": text}

    async def satellite_agents(arguments: dict) -> dict:
        runtime = bundle.get(arguments.get("project_root"))
        lines = []
        for spec in runtime["registry"].all():
            lines.append(
                f"{spec.id}: {spec.name} | caps={', '.join(spec.capabilities)} "
                f"| tools={', '.join(spec.tools)}"
            )
        return {"isError": False, "text": "\n".join(lines) or "(catálogo vacío)"}

    async def satellite_trace(arguments: dict) -> dict:
        """Lo que hizo una tarea: tools del piloto y delegaciones."""
        task_id = arguments.get("task_id", "")
        runtime = bundle.get(arguments.get("project_root"))
        try:
            state = await asyncio.wait_for(runtime["store"].load_state(task_id), timeout=10)
        except Exception as exc:  # noqa: BLE001
            return {"isError": True, "text": f"error: {exc}"}
        trace = (state or {}).get("trace")
        if trace is None:
            return {"isError": False, "text": f"sin traza para {task_id}"}
        payload = {"status": (state or {}).get("status"), **trace}
        return {"isError": False, "text": json.dumps(payload, ensure_ascii=False)}

    async def satellite_status(arguments: dict) -> dict:
        task_id = arguments.get("task_id", "")
        runtime = bundle.get(arguments.get("project_root"))
        try:
            events = await asyncio.wait_for(
                runtime["store"].load_events(task_id), timeout=10
            )
            recent = [e.event_type.value for e in events[-10:]]
            return {"isError": False, "text": f"eventos={len(events)} recientes={recent}"}
        except Exception as exc:  # noqa: BLE001
            return {"isError": True, "text": f"error: {exc}"}

    async def satellite_cancel(arguments: dict) -> dict:
        """Pide cancelar la ejecución en curso del proyecto (cooperativa, entre nodos).

        Antes sólo soltaba el runtime de la caché, que no detenía nada: ahora se pide la
        cancelación de verdad y se dice si había algo que cancelar.
        """
        task_id = str(arguments.get("task_id") or "").strip()
        # Sin id se cancelan todas las ejecuciones en curso (el cliente puede no
        # conocer el id: ``satellite_run`` sólo lo devuelve al terminar).
        targets = (
            [(task_id, bundle._active.get(task_id))] if task_id else list(bundle._active.items())
        )
        cancelled: list[str] = []
        for target_id, root in targets:
            if not isinstance(root, str):
                continue
            orchestrator = bundle.get(root)["orchestrator"]
            cancel = getattr(orchestrator, "cancel", None)
            if callable(cancel) and cancel(target_id, "cancelado desde MCP").get("cancelled"):
                cancelled.append(target_id)
        if not cancelled:
            return {"isError": False, "text": f"no hay ejecución que cancelar para {task_id}"}
        return {
            "isError": False,
            "text": f"cancelación pedida para {', '.join(cancelled)}; el turno se detiene "
            "en el siguiente punto seguro y lo ya hecho queda",
        }

    async def satellite_approvals(arguments: dict) -> dict:
        """Propuestas que esperan ahora la respuesta del cliente (tool de solo lectura)."""
        task_id = str(arguments.get("task_id", ""))
        pending = bundle.approval.pending()
        if task_id:
            pending = [item for item in pending if item.get("task_id") == task_id]
        if not pending:
            return {"isError": False, "text": "(sin propuestas pendientes)"}
        lines = [
            f"{item.get('proposal_id')}: {item.get('kind')} | task={item.get('task_id')} "
            f"| {item.get('summary')}"
            for item in pending
        ]
        return {
            "isError": False,
            "text": "\n".join(lines)
            + "\n\nEsperan la respuesta del cliente a la pregunta de aprobación "
            "(elicitación); sin respuesta se rechazan.",
        }

    return {
        "satellite_run": satellite_run,
        "satellite_agents": satellite_agents,
        "satellite_trace": satellite_trace,
        "satellite_status": satellite_status,
        "satellite_cancel": satellite_cancel,
        "satellite_approvals": satellite_approvals,
    }


TOOLS_LIST = [
    {
        "name": "satellite_run",
        "description": "Runs a complete Satellite task (goal) and returns the result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "project_root": {"type": "string", "description": "Project root (optional)."},
                "task_id": {
                    "type": "string",
                    "description": "Optional id chosen by the client, to cancel the run while it is in progress.",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "satellite_agents",
        "description": "Lists the available agent catalog.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "satellite_trace",
        "description": "Shows what a task did: the pilot's tools and its delegations.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "project_root": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "satellite_status",
        "description": "Current status of a task (recent events).",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "project_root": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "satellite_cancel",
        "description": (
            "Requests cancellation of a running task. Without task_id it cancels every "
            "run in progress."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "satellite_approvals",
        "description": (
            "Lists the proposals awaiting a human decision (read-only). "
            "Approvals are resolved in the CLI or the TUI: MCP does not approve."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "project_root": {"type": "string"},
            },
            "required": [],
        },
    },
]
