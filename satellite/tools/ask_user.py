"""Tool `ask_user`: el agente que conduce el turno le hace una pregunta al usuario.

Llamarla **termina el turno**: el runtime deja de pedir más llamadas al modelo y el
turno acaba en ``needs_user_input`` con la pregunta y sus opciones. El mensaje siguiente
del usuario llega marcado como respuesta a esa pregunta, así que «el segundo», «sí» o
el id de una opción se resuelven sin heurísticas (docs/PLAN-flujo-piloto.md, fase 3).
"""

from __future__ import annotations

from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext

STATE_KEY = "ask_user"
MAX_CHOICES = 6


def normalize_question(arguments: dict) -> dict | None:
    """Pregunta y opciones validadas (``None`` si no hay pregunta)."""
    question = str(arguments.get("question") or "").strip()
    if not question:
        return None
    choices: list[dict] = []
    seen: set[str] = set()
    raw = arguments.get("choices")
    for index, item in enumerate(raw if isinstance(raw, list) else [], 1):
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("title") or "").strip()
        if not label:
            continue
        choice_id = str(item.get("id") or f"option_{index}").strip()
        if choice_id in seen:
            continue
        seen.add(choice_id)
        choices.append(
            {
                "id": choice_id,
                "label": label,
                "detail": str(item.get("detail") or item.get("summary") or "").strip(),
            }
        )
        if len(choices) >= MAX_CHOICES:
            break
    recommended = str(arguments.get("recommended") or "").strip()
    if recommended not in seen:
        recommended = ""
    return {"question": question, "choices": choices, "recommended": recommended}


def render_question(payload: dict) -> str:
    """La pregunta como texto legible en cualquier superficie (también la CLI)."""
    lines = [str(payload.get("question") or "").strip()]
    choices = payload.get("choices") or []
    recommended = payload.get("recommended") or ""
    if choices:
        lines.append("")
    for index, choice in enumerate(choices, 1):
        mark = " ★" if choice.get("id") == recommended else ""
        line = f"{index}. **{choice.get('label', '')}**{mark}"
        if choice.get("detail"):
            line += f" — {choice['detail']}"
        lines.append(line)
    return "\n".join(lines).strip()


class AskUserTool(Tool):
    """Pregunta al usuario y termina el turno a la espera de su respuesta."""

    @property
    def id(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Asks the user a question and ENDS the turn until they answer. Use it only "
            "when the ambiguity changes the work, or to offer alternative approaches. "
            "Optional `choices` (2-6 items with id, label, detail) and `recommended` "
            "(a choice id). Write the question and choices in the user's language."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("conversation",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask."},
                "choices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "detail": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                },
                "recommended": {"type": "string"},
            },
            "required": ["question"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        payload = normalize_question(arguments or {})
        if payload is None:
            return ToolResult(success=False, error="se requiere el argumento 'question'")
        state = getattr(tool_ctx, "task_state", None)
        if not isinstance(state, dict):
            return ToolResult(success=False, error="no hay un turno al que preguntar")
        state[STATE_KEY] = payload
        return ToolResult(
            success=True,
            output={"asked": True, "note": "The turn ends now; wait for the user."},
        )
