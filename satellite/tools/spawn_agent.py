"""Tool ``spawn_agent``: create one isolated subagent for a parent agent."""

from __future__ import annotations

from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext


class SpawnAgentTool(Tool):
    """Runs one child concurrently with the other spawn calls of the same turn."""

    @property
    def id(self) -> str:
        return "spawn_agent"

    @property
    def description(self) -> str:
        return (
            "Creates one isolated subagent for a self-contained part of your task. "
            "Only specialized agents may use it, never the orchestrator and never a "
            "subagent. Calls made in the same turn run concurrently; the parent waits "
            "for all of them before integrating. At most four children per parent."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("delegation", "parallelism")

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Complete, self-contained instruction for the child: exact work, "
                        "interfaces/contracts, restrictions and expected result."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Minimum context the child needs. It does not receive the parent "
                        "conversation or tool state."
                    ),
                },
                "files_owned": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Files or regions the child may modify. Use 'path' for a whole "
                        "file or 'path::region' for a named function/class/region. "
                        "Ownership must not overlap with another child."
                    ),
                },
                "files_readonly": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files the child may read but must not modify.",
                },
            },
            "required": ["task", "context", "files_owned", "files_readonly"],
            "additionalProperties": False,
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        if getattr(tool_ctx, "is_child", False):
            return ToolResult(
                success=False,
                error="Subagents cannot create other agents.",
                error_code="subagent_cannot_spawn",
            )
        manager = getattr(tool_ctx, "spawn_manager", None)
        if manager is None:
            return ToolResult(
                success=False,
                error="spawn_agent is not available in this context.",
                error_code="spawn_unavailable",
            )
        task = arguments.get("task")
        if not isinstance(task, str) or not task.strip():
            return ToolResult(
                success=False,
                error="spawn_agent requires a non-empty 'task'.",
                error_code="invalid_spawn_task",
            )
        context = arguments.get("context", "")
        if not isinstance(context, str):
            context = str(context)
        owned = _string_list(arguments.get("files_owned"))
        readonly = _string_list(arguments.get("files_readonly"))
        result = await manager.spawn(
            task=task,
            context=context,
            files_owned=owned,
            files_readonly=readonly,
        )
        return ToolResult(
            success=True,
            output=result.to_dict(),
            error=None if result.status == "completed" else result.error or result.summary,
        )


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    return []
