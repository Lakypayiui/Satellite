"""Conversación multi-turno sobre el orquestador.

Reutilizable por TUI/CLI/Web/MCP: mantiene el historial, lo resume para el
planner (como DATO no confiable) y ejecuta cada mensaje con el runtime, de modo
que un seguimiento ("agrega esto") se entienda en el contexto de lo anterior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from satellite.approval.base import ApprovalPort
from satellite.context.compaction import (
    CompactionResult,
    ContextUsage,
    estimate_history,
    should_compact,
    strip_code_fences,
    summarize_turns,
)
from satellite.core.task import Task, TaskResult
from satellite.retrieval.sanitize import sanitize

MAX_RECAP_TURNS = 8
MAX_TURN_CHARS = 1200
CONVERSATION_FILE = "conversation.json"

_ROLE_LABEL = {"user": "usuario", "assistant": "asistente"}


def conversation_path(project_root: Path | str) -> Path:
    """Ruta de la conversación persistida (``.satellite/state/conversation.json``)."""
    return Path(project_root) / ".satellite" / "state" / CONVERSATION_FILE


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
    compaction: dict | None = None


class ChatSession:
    """Conversación con el agente (orquestador) sobre un proyecto."""

    def __init__(
        self,
        project_root: Path,
        orchestrator,
        *,
        max_recap_turns: int = MAX_RECAP_TURNS,
        settings=None,
        persist: bool = True,
    ) -> None:
        self.project_root = Path(project_root)
        self.orchestrator = orchestrator
        self.max_recap_turns = max_recap_turns
        self.history: list[Turn] = []
        self._settings = settings
        self._discovered: int | None = None
        self.persist = persist

    # --- persistencia de la conversación ---

    def load(self) -> int:
        """Carga la conversación guardada; devuelve cuántos turnos había."""
        path = conversation_path(self.project_root)
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        raw = data.get("turns") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return 0
        loaded: list[Turn] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                loaded.append(Turn.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        self.history = loaded
        return len(loaded)

    def save(self) -> None:
        """Guarda la conversación (atómico y tolerante)."""
        if not self.persist:
            return
        from datetime import datetime, timezone

        path = conversation_path(self.project_root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "turns": [turn.to_dict() for turn in self.history],
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            return

    def reset(self) -> None:
        """Empieza una conversación nueva (y borra la guardada)."""
        self.history.clear()
        if not self.persist:
            return
        path = conversation_path(self.project_root)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                return

    def add(self, role: str, content: str, *, task_id: str = "", status: str = "") -> Turn:
        """Añade un turno (sanitizado). El historial es **append-only**: nunca se

        reescribe ni se reordena; el único punto que puede acortarlo es
        :meth:`compact_now`/:meth:`compact`. Se persiste al vuelo.
        """
        turn = Turn(
            role=role,
            content=sanitize(content, max_chars=MAX_TURN_CHARS),
            task_id=task_id,
            status=status,
        )
        self.history.append(turn)
        self.save()
        return turn

    def turns(self) -> tuple[Turn, ...]:
        """Copia inmutable del historial completo."""
        return tuple(self.history)

    def compact(self, keep: int | None = None, *, summary: str = "") -> int:
        """ÚNICO punto de compactación del historial (de uso **manual**).

        Conserva los últimos ``keep`` turnos y, si se pasa ``summary``, deja un
        único turno ``role="summary"`` con ese resumen en lugar de los descartados.
        Devuelve cuántos turnos se descartaron. Para compactar con resumen del
        LLM (o por umbral de contexto) usa :meth:`compact_now`.
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

    # --- contexto (ventana del modelo, medición y compactación) ---

    def settings(self):
        """Configuración del kernel (se carga perezosamente)."""
        if self._settings is None:
            from satellite.cli.runtime_builder import CONFIG_PATH
            from satellite.kernel.policy import load_settings

            self._settings = load_settings(self.project_root / CONFIG_PATH)
        return self._settings

    def provider(self):
        """Proveedor LLM del runtime (``None`` si no está construido)."""
        return getattr(getattr(self.orchestrator, "services", None), "provider", None)

    def provider_window(self) -> tuple[str, str]:
        """``(modelo, base_url)`` configurados, para resolver la ventana."""
        path = self.project_root / CONFIG_PATH
        data: dict = {}
        if path.exists():
            try:
                import json

                loaded = json.loads(path.read_text(encoding="utf-8"))
                data = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                data = {}
        llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
        model = str(llm.get("model") or data.get("model") or "")
        base_url = str(llm.get("base_url") or data.get("base_url") or "")
        if not model:
            provider = self.provider()
            model = str(getattr(provider, "model", "") or "")
        return model, base_url

    def context_usage(self, pending_text: str = "") -> ContextUsage:
        """Cuánto contexto se está usando (real de la última llamada + estimado).

        La ventana del modelo sale de la data del proveedor activo (su ``/models``);
        se descubre una vez y queda recordada en disco.
        """
        from satellite.context.compaction import project_usage

        return project_usage(
            self.project_root,
            turns=self.history,
            pending_text=pending_text,
            config=self.settings().compaction,
        )

    def should_compact(self, pending_text: str = "") -> bool:
        """True si el uso alcanzó el umbral configurado (85 % por defecto)."""
        return should_compact(self.context_usage(pending_text), self.settings().compaction)

    async def compact_now(self, *, keep: int | None = None, summary: str = "") -> CompactionResult:
        """Compacta el historial ahora: resumen + últimos turnos.

        El resumen lo hace el LLM si hay proveedor; si no (o si falla), se usa un
        recorte determinista. Nunca deja el historial vacío.
        """
        config = self.settings().compaction
        limit = config.keep if keep is None else max(2, int(keep))
        before = list(self.history)
        tokens_before = estimate_history(before)
        if len(before) <= limit:
            return CompactionResult(
                dropped=0,
                kept=len(before),
                summary="",
                method="sin-cambios",
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        dropped_turns = before[:-limit]
        kept_turns = before[-limit:]
        if summary:
            text, method = summary, "manual"
        else:
            text, method = await summarize_turns(dropped_turns, self.provider())
        text = strip_code_fences(text)
        header = (
            [Turn(role="summary", content=text, status="compactado")]
            if text
            else []
        )
        candidate = header + list(kept_turns)
        after_tokens = estimate_history(candidate)
        if after_tokens >= tokens_before:
            # compactar no reduciría nada (conversación pequeña): no tocamos nada
            return CompactionResult(
                dropped=0,
                kept=len(before),
                summary="",
                method="sin-efecto",
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )
        self.history = candidate
        self.save()
        return CompactionResult(
            dropped=len(dropped_turns),
            kept=len(kept_turns),
            summary=text,
            method=method,
            tokens_before=tokens_before,
            tokens_after=after_tokens,
        )

    async def maybe_compact(self, pending_text: str = "") -> CompactionResult | None:
        """Compacta automáticamente si se alcanzó el umbral; ``None`` si no hacía falta."""
        if not self.should_compact(pending_text):
            return None
        result = await self.compact_now()
        return result if result.dropped else None

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
        """Envía un mensaje: compacta si hace falta, planifica/ejecuta y guarda."""
        compaction = await self.maybe_compact(text)
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
            return ChatReply(
                text=text,
                status="failed",
                error=str(exc),
                compaction=compaction.to_dict() if compaction else None,
            )

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
            compaction=compaction.to_dict() if compaction else None,
        )

    async def choose(self, option, *, approval: ApprovalPort | None = None) -> ChatReply:
        """Elige una alternativa del modo plan y continúa con ella."""
        steps = "; ".join(getattr(option, "approach", ()) or ()) or "sin pasos detallados"
        text = (
            f"Opción elegida: {getattr(option, 'title', '')}. "
            f"{getattr(option, 'summary', '')} Pasos: {steps}. Procede con esta opción."
        )
        return await self.send(
            text,
            approval=approval,
            # execute_choice: es una orden de ejecución, no una pregunta, así que el
            # orquestador no debe atajarla con el catálogo ni con respuesta directa.
            extra_metadata={"allow_plan_options": False, "execute_choice": True},
        )


_OUTPUT_TEXT_KEYS = ("content", "answer", "text", "summary", "result", "output")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def readable_output(output: dict | None, *, limit: int = 1200) -> str:
    """El texto legible de un output de nodo, sea cual sea su forma.

    Un nodo con ``output_schema`` devuelve las claves que dicte el schema (p.ej.
    ``skill``/``result``), así que mirar sólo ``content`` dejaba al usuario sin
    respuesta. Como último recurso se serializa el JSON: mejor eso que el silencio.
    """
    if not isinstance(output, dict) or not output:
        return ""
    for key in _OUTPUT_TEXT_KEYS:
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value.strip(), limit)
        if isinstance(value, (list, tuple)) and value and all(
            isinstance(item, str) for item in value
        ):
            return _clip(" ".join(str(item) for item in value), limit)
    try:
        import json

        rendered = json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - un dict simple siempre serializa
        return ""
    return _clip(rendered, limit) if rendered not in ("", "{}", "null") else ""


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
    content = readable_output(output)
    if content:
        # Camino del grafo: el texto del último nodo es la respuesta, tenga `content`
        # o las claves que dicte su `output_schema`.
        return content
    return result.summary or "(sin respuesta)"
