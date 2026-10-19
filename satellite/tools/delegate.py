"""Tool `delegate`: permite a un agente invocar a otro agente declarado."""

from __future__ import annotations

from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext


class DelegateTool(Tool):
    """Delega una tarea concreta en otro agente (declarado en ``can_call``).

    El sub-agente arranca con contexto limpio y devuelve un resumen; el gestor
    de delegación aplica presupuesto, ciclos y permisos.
    """

    @property
    def id(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        return (
            "Delegates a concrete piece of work to another agent in your can_call "
            "list and returns its summary."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("delegation",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent to delegate to."},
                "task": {
                    "type": "object",
                    "description": "Concrete work to delegate (goal + params).",
                },
            },
            "required": ["agent_id"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        manager = getattr(tool_ctx, "delegation", None)
        ctx = getattr(tool_ctx, "delegation_ctx", None)
        if manager is None or ctx is None:
            return ToolResult(
                success=False, error="delegación no disponible en este contexto"
            )
        agent_id = arguments.get("agent_id", "")
        if not isinstance(agent_id, str) or not agent_id.strip():
            return ToolResult(success=False, error="se requiere el argumento 'agent_id'")
        task = arguments.get("task")
        if not isinstance(task, dict):
            task = {"goal": str(task)} if task else {}

        caller_spec = getattr(tool_ctx, "active_spec", None)
        result = await manager.delegate(
            agent_id=agent_id,
            task=task,
            parent_ctx=ctx,
            project_root=tool_ctx.project_root,
            caller_spec=caller_spec,
            approval=getattr(tool_ctx, "approval", None),
            cancel=getattr(tool_ctx, "cancel", None),
        )
        if not result.ok:
            return ToolResult(success=False, error=result.error or "delegación fallida")
        return ToolResult(
            success=True,
            output={
                "agent_id": result.agent_id,
                "summary": result.summary,
                "status": result.status,
            },
        )
