"""Tool puente request_context: pide contexto adicional al ContextEngine."""

from __future__ import annotations

from satellite.core.context_models import ContextRef, ContextRefKind
from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext


class RequestContextTool(Tool):
    """Pide contexto adicional del proyecto (archivos o patrones) al engine."""

    @property
    def id(self) -> str:
        return "request_context"

    @property
    def description(self) -> str:
        return (
            "Pide contexto adicional del proyecto (archivos, directorios, "
            "patrones, símbolos o resumen) cuando necesitas más información."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("context",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["path", "dir", "pattern", "symbol", "summary"],
                            },
                            "value": {"type": "string"},
                        },
                        "required": ["kind", "value"],
                    },
                    "description": "Referencias de contexto a resolver.",
                }
            },
            "required": ["refs"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        engine = getattr(tool_ctx, "engine", None)
        if engine is None:
            return ToolResult(
                success=False,
                error="context engine no disponible",
            )
        raw_refs = arguments.get("refs", [])
        try:
            refs = [
                ContextRef(kind=ContextRefKind(r["kind"]), value=r["value"])
                for r in raw_refs
            ]
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                success=False,
                error=f"refs inválidos: {exc}",
            )
        bundle = await engine.resolve(refs)
        sections = []
        for section in bundle.sections:
            sections.append(
                {
                    "kind": section.ref.kind.value,
                    "value": section.ref.value,
                    "content": section.content,
                }
            )
        return ToolResult(success=True, output={"sections": sections})
