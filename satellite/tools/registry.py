"""Registro por defecto de tools."""

from __future__ import annotations

from satellite.core.events import EventType, ExecutionEvent
from satellite.core.errors import ToolError
from satellite.core.tool_models import ToolCall, ToolResult, ToolSchema
from satellite.security.base import SecurityPolicy
from satellite.tools.base import Tool, ToolContext, ToolRegistry


class DefaultToolRegistry(ToolRegistry):
    """Registro de tools con política de seguridad como red final."""

    def __init__(self, policy: SecurityPolicy | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.policy = policy

    def register(self, tool: Tool) -> None:
        """Registra una tool; ToolError si el id ya existe."""
        if tool.id in self._tools:
            raise ToolError(f"Tool ya registrada: '{tool.id}'")
        self._tools[tool.id] = tool

    def get(self, tool_id: str) -> Tool:
        """Devuelve la tool; ToolError si no existe."""
        try:
            return self._tools[tool_id]
        except KeyError:
            raise ToolError(f"Tool no registrada: '{tool_id}'") from None

    def all(self) -> list[Tool]:
        """Todas las tools registradas."""
        return list(self._tools.values())

    def list_for(self, tool_ids: list[str]) -> list[Tool]:
        """Lista de tools; ToolError si algún id no existe."""
        return [self.get(tid) for tid in tool_ids]

    def schemas_for(self, tool_ids: list[str]) -> list[ToolSchema]:
        """Schemas de las tools; ToolError si algún id no existe."""
        return [tool.schema() for tool in self.list_for(tool_ids)]

    async def execute(self, call: ToolCall, tool_ctx: ToolContext) -> ToolResult:
        """Ejecuta la tool con política como red de seguridad final.

        - Si la política deniega → ``ToolResult(success=False, error=...)``
          (NO excepción: el LLM debe ver el error y poder corregir).
        - Si la tool lanza ToolError → ToolResult con error.
        """
        tool = self.get(call.name)
        policy = self.policy or tool_ctx.policy
        if policy is not None and not policy.check_tool(call.name, call.arguments):
            result = ToolResult(
                call_id=call.id,
                success=False,
                error=f"permiso denegado: tool '{call.name}' no está permitida",
                error_code="tool_not_allowed",
            )
            self._emit_called(call, tool_ctx, result)
            return result
        try:
            result = await tool.execute(call.arguments, tool_ctx)
        except ToolError as exc:
            result = ToolResult(
                call_id=call.id,
                success=False,
                error=str(exc),
                error_code=str(getattr(exc, "code", "") or ""),
                # Si el error trae detalles estructurados (p.ej. la ruta denegada),
                # viajan aquí: el runtime los usa para pedir permiso sin parsear texto.
                output=getattr(exc, "details", None),
            )
        self._emit_called(call, tool_ctx, result)
        return result

    def _emit_called(
        self, call: ToolCall, tool_ctx: ToolContext, result: ToolResult
    ) -> None:
        if tool_ctx.sink is None:
            return
        tool_ctx.sink.emit(
            ExecutionEvent(
                event_type=EventType.TOOL_CALLED,
                task_id=tool_ctx.task_id,
                node_id=tool_ctx.node_id or None,
                tool_name=call.name,
                payload={
                    "arguments": call.arguments,
                    "success": result.success,
                    "error": result.error,
                },
            )
        )
