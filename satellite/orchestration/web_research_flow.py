"""Ruta determinista para investigar en internet.

Las peticiones explícitas de búsqueda/consulta web se atienden con el agente base
``web_researcher`` en vez de dejarlas al planificador o a la respuesta directa
del chat. El flujo sólo enruta; las tools ``web_search``/``web_fetch`` siguen
haciendo el trabajo y tratando el contenido como dato no confiable.
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

DEFAULT_AGENT_ID = "web_researcher"

_STATUS = {
    AgentStatus.FAILED: TaskStatus.FAILED,
    AgentStatus.NEEDS_USER_INPUT: TaskStatus.NEEDS_USER_INPUT,
    AgentStatus.CANCELLED: TaskStatus.CANCELLED,
    AgentStatus.SKIPPED: TaskStatus.FAILED,
    AgentStatus.OK: TaskStatus.OK,
}


class WebResearchFlow:
    """Ejecuta ``web_researcher`` para una petición de investigación web."""

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
        """Devuelve el resultado, o ``None`` si el agente no está disponible."""
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
            node_id="web",
            project_root=task.project_root or self.project_root,
            user_language=language,
            approval=getattr(self.services, "approval", None),
            cancel=getattr(self.services, "cancel", None),
        )
        try:
            result = await agent.execute(
                {"node_id": "web", "goal": task.goal}, ctx
            )
        except Exception as exc:  # noqa: BLE001 — el flujo nunca debe romper la tarea
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                summary=_error_message(language, str(exc)),
                error=str(exc),
                output={"mode": "web_research", "error": str(exc)},
            )

        output = dict(result.output or {})
        summary = str(
            output.get("summary") or output.get("content") or result.summary or ""
        ).strip()
        sources = output.get("sources")
        if not isinstance(sources, list):
            sources = []
        sources = [
            {"title": str(item.get("title") or ""), "url": str(item.get("url") or "")}
            for item in sources[:20]
            if isinstance(item, dict)
        ]
        if not summary:
            summary = _error_message(language, result.error or "")
        return TaskResult(
            task_id=task.task_id,
            status=_STATUS.get(result.status, TaskStatus.OK),
            summary=summary,
            error=result.error,
            output={
                "mode": "web_research",
                "content": summary,
                "sources": sources,
                "user_language": language,
            },
        )


def _error_message(language: str, detail: str) -> str:
    suffix = f" ({detail})" if detail else ""
    if language == "en":
        return "I could not complete the web research." + suffix
    return "No pude completar la investigación en internet." + suffix


__all__ = ["WebResearchFlow", "DEFAULT_AGENT_ID"]
