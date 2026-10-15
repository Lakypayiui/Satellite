"""Conversación multi-turno sobre el orquestador.

Reutilizable por TUI/CLI/Web/MCP: mantiene el historial, lo resume para el
planner (como DATO no confiable) y ejecuta cada mensaje con el runtime, de modo
que un seguimiento ("agrega esto") se entienda en el contexto de lo anterior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from satellite.approval.base import ApprovalPort
from satellite.core.task import Task, TaskResult
from satellite.retrieval.sanitize import sanitize

MAX_RECAP_TURNS = 8
MAX_TURN_CHARS = 1200

_ROLE_LABEL = {"user": "usuario", "assistant": "asistente"}


@dataclass(frozen=True)
class Turn:
    """Un turno de la conversación."""

    role: str  # user | assistant
    content: str
    task_id: str = ""
    status: str = ""

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "task_id": self.task_id,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Turn":
        return cls(
            role=str(d.get("role", "")),
            content=str(d.get("content", "")),
            task_id=str(d.get("task_id", "")),
            status=str(d.get("status", "")),
        )


@dataclass(frozen=True)
class ChatReply:
    """Respuesta del agente a un mensaje."""

    text: str
    task_id: str = ""
    status: str = ""
    error: str | None = None
    output: dict = field(default_factory=dict)


class ChatSession:
    """Conversación con el agente (orquestador) sobre un proyecto."""

    def __init__(
        self,
        project_root: Path,
        orchestrator,
        *,
        max_recap_turns: int = MAX_RECAP_TURNS,
    ) -> None:
        self.project_root = Path(project_root)
        self.orchestrator = orchestrator
        self.max_recap_turns = max_recap_turns
        self.history: list[Turn] = []

    def reset(self) -> None:
        """Empieza una conversación nueva."""
        self.history.clear()

    def add(self, role: str, content: str, *, task_id: str = "", status: str = "") -> Turn:
        """Añade un turno (sanitizado). El historial es **append-only**: nunca se

        reescribe ni se reordena; el único punto que puede acortarlo es
        :meth:`compact`, y **no** se llama solo.
        """
        turn = Turn(
            role=role,
            content=sanitize(content, max_chars=MAX_TURN_CHARS),
            task_id=task_id,
            status=status,
        )
        self.history.append(turn)
        return turn

    def turns(self) -> tuple[Turn, ...]:
        """Copia inmutable del historial completo."""
        return tuple(self.history)

    def compact(self, keep: int | None = None, *, summary: str = "") -> int:
        """ÚNICO punto de compactación del historial (de uso **manual**).

        Conserva los últimos ``keep`` turnos y, si se pasa ``summary``, deja un
        único turno ``role="summary"`` con ese resumen en lugar de los descartados.
        Devuelve cuántos turnos se descartaron. No se invoca automáticamente en
        ningún flujo: el historial crece sólo con :meth:`add`.
        """
        limit = self.max_recap_turns if keep is None else max(0, int(keep))
        if len(self.history) <= limit:
            return 0
        dropped = len(self.history) - limit
        kept = self.history[-limit:] if limit else []
        prefix = (
            [Turn(role="summary", content=sanitize(summary, max_chars=MAX_TURN_CHARS), status="compactado")]
            if summary
            else []
        )
        self.history = prefix + kept
        return dropped

    def recent(self, limit: int | None = None) -> tuple[Turn, ...]:
        """Últimos turnos (los más recientes al final)."""
        return tuple(self.history[-(limit or self.max_recap_turns):])

    def recap(self, limit: int | None = None) -> str:
        """Resumen textual de la conversación para el planner."""
        turns = self.recent(limit)
        if not turns:
            return ""
        return "\n".join(
            f"- {_ROLE_LABEL.get(turn.role, turn.role)}: {turn.content}" for turn in turns
        )

    async def send(
        self,
        text: str,
        *,
        approval: ApprovalPort | None = None,
        plan_mode: bool = False,
        extra_metadata: dict | None = None,
    ) -> ChatReply:
        """Envía un mensaje: planifica/ejecuta y guarda la respuesta."""
        history = [turn.to_dict() for turn in self.recent()]
        self.add("user", text)

        metadata: dict = {"history": history, "conversation": True}
        if plan_mode:
            metadata["plan_mode"] = True
        if extra_metadata:
            metadata.update(extra_metadata)

        task = Task(goal=text, project_root=self.project_root, metadata=metadata)
        services = getattr(self.orchestrator, "services", None)
        if services is not None and approval is not None:
            services.approval = approval

        try:
            result = await self.orchestrator.execute(task)
        except Exception as exc:  # noqa: BLE001 — la conversación no debe romperse
            text = f"No pude completarlo: {exc}"
            self.add("assistant", text, status="failed")
            return ChatReply(text=text, status="failed", error=str(exc))

        reply_text = reply_from_result(result)
        self.add(
            "assistant",
            reply_text,
            task_id=result.task_id,
            status=result.status.value,
        )
        return ChatReply(
            text=reply_text,
            task_id=result.task_id,
            status=result.status.value,
            error=result.error,
            output=dict(result.output or {}),
        )

    async def choose(self, option, *, approval: ApprovalPort | None = None) -> ChatReply:
        """Elige una alternativa del modo plan y continúa con ella."""
        steps = "; ".join(getattr(option, "approach", ()) or ()) or "sin pasos detallados"
        text = (
            f"Opción elegida: {getattr(option, 'title', '')}. "
            f"{getattr(option, 'summary', '')} Pasos: {steps}. Procede con esta opción."
        )
        return await self.send(
            text, approval=approval, extra_metadata={"allow_plan_options": False}
        )


def reply_from_result(result: TaskResult) -> str:
    """Convierte el ``TaskResult`` en el mensaje que ve el usuario."""
    output = result.output or {}
    mode = output.get("mode")
    if mode == "plan_options":
        options = output.get("plan_options") or {}
        count = len(options.get("options") or [])
        return f"Te propongo {count} alternativas. Elige una o describe tu propio enfoque."
    if mode == "direct_reply":
        return str(output.get("content") or result.summary or "")
    if mode == "needs_clarification":
        return str(output.get("question") or result.summary or "")
    if mode == "capability_gap":
        gap = output.get("capability_gap") or {}
        missing = gap.get("missing", "")
        return f"Me falta una capacidad: {missing}. ¿Quieres que la cree?"
    if result.error:
        return f"No pude completarlo: {result.error}"
    return result.summary or "(sin respuesta)"
