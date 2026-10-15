"""MCP handlers: each tool calls the runtime (via the Etapa 7 builder).

Aprobaciones: MCP **no** aprueba nada (``McpApproval`` rechaza y deja la
propuesta a la vista). Un host podría inyectar la aprobación en el prompt, así
que la decisión se toma siempre con un humano delante: CLI o TUI.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from satellite.approval.mcp import McpApproval
from satellite.cli.runtime_builder import build_runtime
from satellite.core.task import Task
from satellite.persistence.store import FileStore

_DEFAULT_ROOT = os.environ.get("SATELLITE_MCP_PROJECT_ROOT")


class RuntimeBundle:
    """Cached runtime per project_root: builder, active orchestrators, bus, store."""

    def __init__(self) -> None:
        self._runtimes: dict[str, dict] = {}
        self._active: dict[str, str] = {}  # task_id -> project_root
        self.approval = McpApproval()

    def get(self, project_root: Path | str | None) -> dict:
        """Build or retrieve the runtime for a project root."""
        root = str(Path(project_root or _DEFAULT_ROOT or Path.cwd()).resolve())
        if root not in self._runtimes:
            orchestrator, bus, store, registry = build_runtime(Path(root))
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
        root = arguments.get("project_root")
        runtime = bundle.get(root)
        task = Task(goal=goal, project_root=Path(root or Path.cwd()).resolve())
        result = await runtime["orchestrator"].execute(task)
        bundle._active[task.task_id] = runtime
        text = f"status: {result.status.value}\nsummary: {result.summary}"
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

    async def satellite_graph(arguments: dict) -> dict:
        task_id = arguments.get("task_id", "")
        runtime = bundle.get(arguments.get("project_root"))
        try:
            graph = await asyncio.wait_for(
                runtime["store"].load_graph(task_id), timeout=10
            )
            return {"isError": False, "text": str(graph.to_dict())}
        except Exception as exc:  # noqa: BLE001
            return {"isError": True, "text": f"error: {exc}"}

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
        task_id = arguments.get("task_id", "")
        bundle._active.pop(task_id, None)
        return {"isError": False, "text": f"cancelación pedida (best-effort) para {task_id}"}

    async def satellite_approvals(arguments: dict) -> dict:
        """Propuestas que esperan una decisión humana (tool de solo lectura)."""
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
            + "\n\nLas aprobaciones se resuelven en el CLI o en la TUI: MCP no aprueba.",
        }

    return {
        "satellite_run": satellite_run,
        "satellite_agents": satellite_agents,
        "satellite_graph": satellite_graph,
        "satellite_status": satellite_status,
        "satellite_cancel": satellite_cancel,
        "satellite_approvals": satellite_approvals,
    }


TOOLS_LIST = [
    {
        "name": "satellite_run",
        "description": "Ejecuta una tarea completa de Satellite (goal) y devuelve el resultado.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "project_root": {"type": "string", "description": "Raíz del proyecto (opcional)."},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "satellite_agents",
        "description": "Lista el catálogo de agentes disponibles.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "satellite_graph",
        "description": "Muestra el grafo serializado de una tarea.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "project_root": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "satellite_status",
        "description": "Estado actual de una tarea (eventos recientes).",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "project_root": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "satellite_cancel",
        "description": "Pide cancelación best-effort de una tarea.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "satellite_approvals",
        "description": (
            "Lista las propuestas que esperan una decisión humana (solo lectura). "
            "Las aprobaciones se resuelven en el CLI o en la TUI: MCP no aprueba."
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
