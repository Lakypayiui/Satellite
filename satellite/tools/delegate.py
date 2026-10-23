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

        mismatch = _language_mismatch(agent_id, task)
        if mismatch:
            # Barato y determinista: un coder de otro lenguaje gasta su presupuesto
            # entero en un trabajo que no es el suyo.
            return ToolResult(success=False, error=mismatch, error_code="wrong_language_agent")

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
        state = getattr(tool_ctx, "task_state", None)
        if isinstance(state, dict):
            # Quien conduce el turno (el piloto) recoge aquí lo que produjeron sus
            # delegaciones: p. ej. el diagrama renderizado, que la UI necesita entero.
            state.setdefault("delegations", []).append(
                {
                    "agent_id": result.agent_id,
                    "ok": result.ok,
                    "status": result.status,
                    "output": dict(result.output or {}),
                    "error": result.error,
                    "tokens_in": getattr(result, "tokens_in", 0),
                    "tokens_out": getattr(result, "tokens_out", 0),
                    "budget_exhausted": getattr(result, "budget_exhausted", False),
                    "files_touched": list(getattr(result, "files_touched", ()) or ()),
                }
            )
        progress = _progress(result)
        if not result.ok:
            return ToolResult(
                success=False,
                error=result.error or "delegación fallida",
                output={"agent_id": result.agent_id, "status": result.status,
                        "details": _clipped(result.output), **progress},
            )
        return ToolResult(
            success=True,
            output={
                "agent_id": result.agent_id,
                "summary": result.summary,
                "status": result.status,
                # El resultado estructurado (archivos cambiados, verificación, fuentes…)
                # para que quien delega pueda contarlo sin adivinar.
                "details": _clipped(result.output),
                **progress,
            },
        )


INCOMPLETE_NOTE = (
    "The agent ran out of tool rounds before finishing. Its work may be partial: "
    "the files in files_touched already contain its changes. Do not repeat that work; "
    "if something is left, delegate only the remaining part, say what is already done "
    "and pass files_read in `context` so the next agent does not explore again."
)


def _progress(result) -> dict:
    """Aviso de trabajo a medias y archivos ya tocados, sólo cuando aplica."""
    out: dict = {}
    files = list(getattr(result, "files_touched", ()) or ())
    if files:
        out["files_touched"] = files
    if getattr(result, "budget_exhausted", False):
        out["incomplete"] = True
        out["note"] = INCOMPLETE_NOTE
        read = list(getattr(result, "files_read", ()) or ())
        if read:
            out["files_read"] = read
    return out


DETAILS_LIMIT = 6000
# Lenguajes que un mismo agente puede tratar indistintamente.
_COMPATIBLE = {"javascript": {"javascript", "typescript"}, "typescript": {"javascript", "typescript"}}


def _language_mismatch(agent_id: str, task: dict) -> str | None:
    """Error si todos los archivos de código de la tarea son de otro lenguaje que el agente."""
    import json
    import re

    from satellite.code.languages import language_for_agent, language_for_path

    agent_language = language_for_agent(agent_id)
    if agent_language is None:
        return None
    try:
        text = json.dumps(task, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    found = {
        language
        for token in re.findall(r"[\w./-]+\.[A-Za-z0-9+#]{1,6}\b", text)
        if (language := language_for_path(token)) is not None
    }
    accepted = _COMPATIBLE.get(agent_language, {agent_language})
    if not found or found & accepted:
        return None
    role = "auditor" if agent_id.endswith("_auditor") else "coder"
    options = ", ".join(sorted(f"{language}_{role}" for language in found))
    return (
        f"'{agent_id}' works on {agent_language}, but the files in this task are "
        f"{', '.join(sorted(found))}. Delegate to {options} instead."
    )


def _clipped(output: dict | None) -> dict | str:
    """El output del agente delegado, acotado para no inflar el contexto de quien llama."""
    import json

    data = dict(output or {})
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    if len(text) <= DETAILS_LIMIT:
        return data
    return text[: DETAILS_LIMIT - 1] + "…"
