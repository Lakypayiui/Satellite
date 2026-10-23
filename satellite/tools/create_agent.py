"""Tool `create_agent`: el piloto crea un agente nuevo por el camino completo.

Llama al expander del runtime: propone con el creador, valida, pide aprobación por el
puerto **del turno** y registra el agente en el catálogo. Delegar en ``agent_creator``
sólo devolvía la propuesta, así que el agente no llegaba a existir
(docs/PLAN-correcciones.md, punto 3).
"""

from __future__ import annotations

from typing import Callable

from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext

# Resultados del expander que dejan un agente utilizable.
_USABLE = frozenset({"created", "extended", "reuse"})


class CreateAgentTool(Tool):
    """Crea (o reutiliza) un agente para una capacidad que falta."""

    def __init__(self, expander: Callable[[], object | None] | None = None) -> None:
        # El expander se construye después que el registro de tools: se lee al
        # ejecutar, no al registrar.
        self._expander = expander or (lambda: None)

    def bind(self, expander: Callable[[], object | None]) -> None:
        self._expander = expander

    @property
    def id(self) -> str:
        return "create_agent"

    @property
    def description(self) -> str:
        return (
            "Creates a new reusable agent for a capability that no listed agent covers, "
            "after the user approves it. `need` describes the capability in one or two "
            "sentences. It may instead reuse an existing agent or ask a question; report "
            "the outcome to the user."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("conversation",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "need": {"type": "string", "description": "The missing capability."}
            },
            "required": ["need"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        need = str((arguments or {}).get("need") or "").strip()
        if not need:
            return ToolResult(success=False, error="se requiere el argumento 'need'")
        expander = self._expander()
        if expander is None:
            return ToolResult(
                success=False, error="este runtime no puede crear agentes (sin expander)"
            )
        outcome = await expander.expand(  # type: ignore[attr-defined]
            need,
            task_id=getattr(tool_ctx, "task_id", "") or "",
            approval_port=getattr(tool_ctx, "approval", None),
        )
        spec = getattr(outcome, "spec", None)
        output = {
            "kind": outcome.kind,
            "agent_id": getattr(spec, "id", None) or outcome.reuse_candidate,
            "question": outcome.question,
            "reason": outcome.reason,
        }
        if outcome.kind in _USABLE:
            output["note"] = (
                "The agent is in the catalog: delegate to it by agent_id "
                "(the user may be asked to confirm the first delegation)."
            )
            return ToolResult(success=True, output=output)
        return ToolResult(
            success=False,
            output=output,
            error=outcome.reason or outcome.question or f"no se creó el agente ({outcome.kind})",
        )


__all__ = ["CreateAgentTool"]
