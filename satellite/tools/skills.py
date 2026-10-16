"""Tools de skills: búsqueda y propuesta (crear una skill requiere aprobación)."""

from __future__ import annotations

from satellite.core.errors import SpecValidationError
from satellite.core.proposal import ProposedSkill
from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext


def _emit_searched(tool_ctx: ToolContext, query: str, hits) -> None:
    """Deja constancia de qué se **consideró** al buscar skills.

    No dice que se vaya a usar ni que se haya ejecutado: eso lo dirán ``SKILL_EXECUTED`` o
    ``SKILL_FAILED`` si llega a pasar. Sin esto, el panel no podía distinguir una skill
    mirada de una usada.
    """
    from satellite.core.events import EventType, ExecutionEvent

    sink = getattr(tool_ctx, "sink", None)
    if sink is None:
        return
    sink.emit(
        ExecutionEvent(
            event_type=EventType.SKILL_SEARCHED,
            task_id=tool_ctx.task_id or "system",
            node_id=tool_ctx.node_id or None,
            tool_name="search_skills",
            payload={
                "query": query,
                "considered": [
                    {"skill_id": hit.id, "score": hit.score} for hit in hits
                ],
            },
        )
    )


class SearchSkillsTool(Tool):
    """Busca skills existentes por descripción (sin LLM)."""

    @property
    def id(self) -> str:
        return "search_skills"

    @property
    def description(self) -> str:
        return "Busca skills ya guardadas por descripción y devuelve id, descripción y score."

    @property
    def categories(self) -> tuple[str, ...]:
        return ("skills",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Descripción de lo que necesitas."},
                "top_k": {"type": "integer", "description": "Número de resultados (default 3)."},
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        registry = getattr(tool_ctx, "skills", None)
        if registry is None:
            return ToolResult(success=False, error="registro de skills no disponible")
        query = str(arguments.get("query", "")).strip()
        if not query:
            return ToolResult(success=False, error="se requiere el argumento 'query'")
        top_k = int(arguments.get("top_k", 3))
        hits = registry.find_similar(query, top_k=top_k)
        _emit_searched(tool_ctx, query, hits)
        return ToolResult(
            success=True,
            output=[
                {"id": hit.id, "description": hit.fields.get("description", ""), "score": hit.score}
                for hit in hits
            ],
        )


class ProposeSkillTool(Tool):
    """Propone guardar una skill; sólo si ya hubo una ejecución exitosa en la tarea.

    No crea la skill: deja la propuesta en ``tool_ctx.task_state["pending_skills"]``
    para que el flujo de aprobación decida.
    """

    @property
    def id(self) -> str:
        return "propose_skill"

    @property
    def description(self) -> str:
        return (
            "Propone guardar como skill reutilizable el código que acaba de funcionar. "
            "Requiere al menos una ejecución exitosa en esta tarea."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("skills",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "snake_case"},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "language": {"type": "string", "enum": ["python", "bash"]},
                "entrypoint": {"type": "string"},
                "script": {"type": "string"},
                "inputs": {"type": "object"},
                "outputs": {"type": "object"},
                "permissions": {"type": "object"},
                "examples": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": "string"},
            },
            "required": ["id", "description", "script", "rationale"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        state = getattr(tool_ctx, "task_state", None) or {}
        if not state.get("successful_runs"):
            return ToolResult(
                success=False,
                error=(
                    "propose_skill: ninguna ejecución exitosa registrada en esta "
                    "tarea (no se proponen skills sin evidencia)"
                ),
            )
        try:
            proposal = ProposedSkill(
                id=str(arguments.get("id", "")),
                description=str(arguments.get("description", "")),
                tags=tuple(arguments.get("tags") or ()),
                language=str(arguments.get("language", "python")),
                entrypoint=str(arguments.get("entrypoint", "main.py")),
                script=str(arguments.get("script", "")),
                inputs=arguments.get("inputs") or {},
                outputs=arguments.get("outputs") or {},
                permissions=arguments.get("permissions") or {},
                examples=tuple(arguments.get("examples") or ()),
                rationale=str(arguments.get("rationale", "")),
                reuse_checked=bool(arguments.get("reuse_checked", False)),
            )
        except (SpecValidationError, TypeError) as exc:
            return ToolResult(success=False, error=f"propuesta de skill inválida: {exc}")

        state.setdefault("pending_skills", []).append(proposal.to_dict())
        return ToolResult(success=True, output=proposal.to_dict())
