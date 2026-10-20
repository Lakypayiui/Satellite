"""Ruta determinista para acciones de escritorio.

Las peticiones que piden abrir/controlar una app, una ventana o el navegador se
atienden directamente con el agente base ``desktop_operator`` en vez de dejarlas
al planificador: así no acaban en un "falta capacidad" ni en una respuesta de
charla. El flujo sólo enruta; los gates (allowlist, aprobación, grants, topes y
auditoría) siguen viviendo en las tools ``desktop_*``.
"""

from __future__ import annotations

from satellite.agents.base import AgentContext
from satellite.core.context_models import (
    ContextBundle,
    ContextRef,
    ContextRefKind,
    ContextSection,
)
from satellite.core.language import detect_user_language
from satellite.core.result import AgentStatus
from satellite.core.task import Task, TaskResult, TaskStatus

DEFAULT_AGENT_ID = "desktop_operator"

_STATUS = {
    AgentStatus.FAILED: TaskStatus.FAILED,
    AgentStatus.NEEDS_USER_INPUT: TaskStatus.NEEDS_USER_INPUT,
    AgentStatus.CANCELLED: TaskStatus.CANCELLED,
    AgentStatus.SKIPPED: TaskStatus.FAILED,
    AgentStatus.OK: TaskStatus.OK,
}


class DesktopFlow:
    """Ejecuta ``desktop_operator`` para una petición de control del escritorio."""

    def __init__(
        self,
        factory,
        project_root,
        *,
        agent_id: str = DEFAULT_AGENT_ID,
        services=None,
    ) -> None:
        self.factory = factory
        self.project_root = project_root
        self.agent_id = agent_id
        self.services = services

    async def run(self, task: Task) -> TaskResult | None:
        """Devuelve el resultado, o ``None`` si el operador no está disponible."""
        if self.factory is None:
            return None
        try:
            if hasattr(self.factory, "can_create") and not self.factory.can_create(
                self.agent_id
            ):
                return None
            agent = self.factory.create(self.agent_id)
        except Exception:  # noqa: BLE001 — agente ausente = se cae al flujo normal
            return None

        language = detect_user_language(task.goal, task.metadata.get("history")) or "es"
        memoria = str(task.metadata.get("session_memory") or "").strip()
        bundle = (
            ContextBundle(
                sections=(
                    ContextSection(
                        ref=ContextRef(ContextRefKind.SUMMARY, "session memory"),
                        content=memoria,
                    ),
                )
            )
            if memoria
            else ContextBundle()
        )
        ctx = AgentContext(
            context=bundle,
            sink=getattr(self.services, "sink", None),
            task_id=task.task_id,
            session_id=str(task.metadata.get("session_id") or ""),
            node_id="desktop",
            project_root=task.project_root or self.project_root,
            user_language=language,
            approval=getattr(self.services, "approval", None),
            cancel=getattr(self.services, "cancel", None),
        )
        try:
            result = await agent.execute(
                {"node_id": "desktop", "goal": task.goal}, ctx
            )
        except Exception as exc:  # noqa: BLE001 — el flujo nunca debe romper la tarea
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                summary=_error_message(language, str(exc)),
                error=str(exc),
                output={"mode": "desktop", "completed": False, "error": str(exc)},
            )

        output = dict(result.output or {})
        summary = str(output.get("summary") or result.summary or "").strip()
        completed = bool(output.get("completed"))
        if not summary:
            summary = _error_message(language, result.error or "")
        return TaskResult(
            task_id=task.task_id,
            status=_STATUS.get(result.status, TaskStatus.OK),
            summary=summary,
            error=result.error,
            output={
                "mode": "desktop",
                "completed": completed,
                "content": summary,
                "user_language": language,
                "operations": output,
            },
        )


def _error_message(language: str, detail: str) -> str:
    suffix = f" ({detail})" if detail else ""
    if language == "en":
        return "I could not complete the desktop action." + suffix
    return "No pude completar la acción de escritorio." + suffix


__all__ = ["DesktopFlow", "DEFAULT_AGENT_ID"]
