"""Conversación multi-turno sobre el orquestador.

Reutilizable por TUI/CLI/Web/MCP: mantiene el historial, lo resume para el
planner (como DATO no confiable) y ejecuta cada mensaje con el runtime, de modo
que un seguimiento ("agrega esto") se entienda en el contexto de lo anterior.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from satellite.approval.base import ApprovalContext, ApprovalPort
from satellite.core.context_models import ApprovedExternalContext
from satellite.context.compaction import (
    CompactionResult,
    ContextUsage,
    estimate_history,
    should_compact,
    strip_code_fences,
    summarize_turns,
)
from satellite.core.language import detect_user_language, reply_fallback
from satellite.core.task import Task, TaskResult
from satellite.retrieval.sanitize import sanitize

MAX_RECAP_TURNS = 8
MAX_TURN_CHARS = 1200
CONVERSATION_FILE = "conversation.json"
# Claves de contexto que sólo fija el flujo de chat (tras aprobación); la metadata libre
# no puede aportarlas.
_RESERVED_CONTEXT_KEYS = ("context_refs", "context_missing", "context_external")

_ROLE_LABEL = {"user": "usuario", "assistant": "asistente"}

# Aceptación en texto libre de la alternativa recomendada del modo plan. Es una orden
# de ejecución sobre el plan ya presentado: no se debe volver a pedir una elección ni
# contestar desde el catálogo.
_RECOMMENDED_RE = re.compile(
    r"\brecomendad[ao]s?\b|\bla que (me )?recomiendas?\b|\bopci[oó]n recomendada\b",
    re.IGNORECASE,
)


def _accepts_recommendation(text: str) -> bool:
    """True si el mensaje acepta la alternativa recomendada del plan pendiente."""
    return bool(_RECOMMENDED_RE.search(str(text or "")))


def _option_instruction(option) -> str:
    """Execution instruction derived from a plan-mode option (internal, English)."""
    steps = (
        "; ".join(getattr(option, "approach", ()) or ())
        or "no detailed steps were provided"
    )
    return (
        f"Chosen option: {getattr(option, 'title', '')}. "
        f"{getattr(option, 'summary', '')} Steps: {steps}. Proceed with this option."
    )


def _lang_text(language: str, es: str, en: str) -> str:
    """Texto de respuesta/aviso según el idioma detectado (``en`` sólo si es inglés)."""
    return en if language == "en" else es


def conversation_path(project_root: Path | str) -> Path:
    """Ruta de la conversación **activa** (``state/conversations/<id>.json``).

    Sigue devolviendo el fichero que hay que leer/escribir, así que todo lo que ya usaba
    esta ruta (CLI, TUI, tests) apunta a la sesión correcta sin cambios.
    """
    from satellite.orchestration.sessions import SessionStore

    return SessionStore(project_root).current_path()


@dataclass(frozen=True)
class Turn:
    """Un turno de la conversación."""

    role: str  # user | assistant
    content: str
    task_id: str = ""
    status: str = ""
    # Artifact produced by the turn (currently a rendered Mermaid diagram). Persisted
    # with the conversation so the graphical chat can show it again after a reload.
    diagram: dict | None = None

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "task_id": self.task_id,
            "status": self.status,
            "diagram": dict(self.diagram) if isinstance(self.diagram, dict) else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Turn":
        raw_diagram = d.get("diagram")
        return cls(
            role=str(d.get("role", "")),
            content=str(d.get("content", "")),
            task_id=str(d.get("task_id", "")),
            status=str(d.get("status", "")),
            diagram=dict(raw_diagram) if isinstance(raw_diagram, dict) else None,
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
    # Métricas de la ejecución (tokens/duración) para el pie de la respuesta en la TUI.
    metrics: dict = field(default_factory=dict)
    # @menciones del mensaje: qué se adjuntó, qué está fuera y qué no existía (para la UI).
    context: dict = field(default_factory=dict)
    # Artefacto del turno (diagrama renderizado o su fuente Mermaid) para la UI.
    diagram: dict | None = None


def _diagram_payload(output: dict | None) -> dict | None:
    """Diagram metadata to persist with the turn (``None`` for other outputs).

    The dedicated diagram flow sets ``mode="diagram"``. A node that used the
    ``render_diagram`` tool may return the tool output directly (``path`` +
    ``rendered``), so that shape is accepted too.
    """
    if not isinstance(output, dict):
        return None
    is_diagram = output.get("mode") == "diagram" or bool(output.get("svg_path"))
    if not is_diagram:
        tool_path = output.get("path")
        is_diagram = (
            bool(output.get("rendered"))
            and isinstance(tool_path, str)
            and tool_path.lower().endswith(".svg")
        )
    if not is_diagram:
        return None
    payload: dict = {}
    for key in ("rendered", "svg_path", "mermaid", "diagram_type"):
        value = output.get(key)
        if isinstance(value, bool) or (isinstance(value, str) and value.strip()):
            payload[key] = value
    if "svg_path" not in payload:
        tool_path = output.get("path")
        if isinstance(tool_path, str) and tool_path.lower().endswith(".svg"):
            payload["svg_path"] = tool_path
    return payload or None


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
        # Alternativas del modo plan pendientes de elección (se consumen en el
        # siguiente mensaje: si acepta la recomendada, se ejecuta sin volver a pedir).
        self._pending_plan = None  # PlanOptions | None

    # --- persistencia de la conversación ---

    def _store(self):
        from satellite.orchestration.sessions import SessionStore

        return SessionStore(self.project_root)

    @property
    def session_id(self) -> str:
        """Id de la sesión activa (``default`` mientras no se elija otra)."""
        return self._store().active_id()

    @property
    def session_name(self) -> str:
        """Nombre de la sesión activa."""
        return self._store().read(self.session_id)[0].name

    def load(self) -> int:
        """Carga la conversación de la sesión activa; devuelve cuántos turnos había."""
        _info, raw = self._store().read(self.session_id)
        loaded: list[Turn] = []
        for item in raw:
            try:
                loaded.append(Turn.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        self.history = loaded
        self._pending_plan = None  # las alternativas no cruzan de sesión
        return len(loaded)

    def save(self) -> None:
        """Guarda la conversación de la sesión activa (atómico y tolerante)."""
        if not self.persist:
            return
        self._store().save(
            self.session_id,
            [turn.to_dict() for turn in self.history],
            name=self.session_name,
        )

    def reset(self) -> None:
        """Empieza una conversación nueva (y borra la guardada de la sesión activa)."""
        self.history.clear()
        self._pending_plan = None
        if not self.persist:
            return
        path = conversation_path(self.project_root)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                return

    # --- sesiones ---

    def sessions(self) -> list:
        """Sesiones guardadas del proyecto (la activa primero)."""
        return self._store().list()

    def new_session(self, name: str = "") -> str:
        """Crea una sesión vacía, la activa y devuelve su id."""
        session_id = self._store().create(name)
        self.history = []
        self._pending_plan = None
        return session_id

    def use_session(self, target: str) -> str | None:
        """Reanuda una sesión por id o por nombre y carga sus turnos."""
        session_id = self._store().switch(target)
        if session_id is None:
            return None
        self.load()
        return session_id

    def rename_session(self, name: str) -> bool:
        """Renombra la sesión activa (conserva los turnos)."""
        if not name.strip():
            return False
        return self._store().rename(self.session_id, name)

    def delete_session(self, session_id: str) -> bool:
        """Borra una conversación; si era la activa, carga la que quede activa."""
        was_active = session_id == self.session_id
        if not self._store().delete(session_id):
            return False
        if was_active:
            self.load()
        return True

    def _summary_provider(self):
        """Proveedor del rol ``context`` (respaldo: el activo)."""
        services = getattr(self.orchestrator, "services", None)
        factory = getattr(services, "factory", None) if services is not None else None
        resolver = getattr(factory, "provider_resolver", None)
        if callable(resolver):
            try:
                provider = resolver("context")
                if provider is not None:
                    return provider
            except Exception:  # noqa: BLE001 — degradar al proveedor activo
                pass
        return getattr(services, "provider", None) if services is not None else None

    def _session_memory(self) -> str:
        """Bloque de resúmenes persistentes de esta conversación."""
        from satellite.context.turn_summaries import (
            load_turn_summaries,
            render_turn_summaries,
        )

        return render_turn_summaries(
            load_turn_summaries(self.project_root, self.session_id)
        )

    async def _record_turn_summary(
        self,
        user_text: str,
        assistant_text: str,
        *,
        task_id: str = "",
        status: str = "",
        language: str = "",
    ) -> None:
        """Resume y guarda el par usuario→modelo. Nunca rompe el turno."""
        if not self.persist:
            return
        from satellite.context.turn_summaries import (
            append_turn_summary,
            make_entry,
            summarize_turn,
        )

        summary = await summarize_turn(
            self._summary_provider(),
            user_text=user_text,
            assistant_text=assistant_text,
            language=language,
        )
        append_turn_summary(
            self.project_root,
            self.session_id,
            make_entry(
                user_summary=str(summary.get("user_summary") or ""),
                assistant_summary=str(summary.get("assistant_summary") or ""),
                task_id=task_id,
                status=status,
            ),
        )

    def _autoname(self, text: str) -> None:
        """Renombra el placeholder con un resumen de 2-3 palabras del primer mensaje."""
        if not self.persist:
            return
        from satellite.orchestration.sessions import (
            DEFAULT_SESSION_NAME,
            NEW_SESSION_NAME,
            summarize_session_name,
        )

        store = self._store()
        info, _turns = store.read(self.session_id)
        current = (info.name or "").strip().lower()
        placeholders = {
            "",
            DEFAULT_SESSION_NAME.lower(),
            NEW_SESSION_NAME.lower(),
            self.session_id.lower(),
        }
        if current not in placeholders and not re.fullmatch(
            r"ses[ií][oó]n\s+[0-9a-z]{4,}", current
        ):
            return
        title = summarize_session_name(text)
        if title:
            store.rename(self.session_id, title)

    def add(
        self,
        role: str,
        content: str,
        *,
        task_id: str = "",
        status: str = "",
        diagram: dict | None = None,
    ) -> Turn:
        """Añade un turno (sanitizado). El historial es **append-only**: nunca se

        reescribe ni se reordena; el único punto que puede acortarlo es
        :meth:`compact_now`/:meth:`compact`. Se persiste al vuelo.
        """
        # User input is capped to keep the stored conversation bounded. Assistant
        # replies are NOT: capping them cut long answers in half (the UI showed
        # "[contenido truncado]" right after 1200 chars).
        limit = MAX_TURN_CHARS if role == "user" else None
        turn = Turn(
            role=role,
            content=sanitize(content, max_chars=limit),
            task_id=task_id,
            status=status,
            diagram=diagram,
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
        from satellite.config import providers as provider_config

        connection = provider_config.active_connection(self.project_root)
        model = str(connection.get("model", ""))
        base_url = str(connection.get("base_url", ""))
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
            # Al compactar se usa el JSON de resúmenes por turno + el compactador.
            from satellite.context.turn_summaries import (
                load_turn_summaries,
                render_turn_summaries,
            )

            persistent = render_turn_summaries(
                load_turn_summaries(self.project_root, self.session_id)
            )
            text, method = await summarize_turns(
                dropped_turns, self.provider(), persistent_summary=persistent
            )
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
        record_text: str | None = None,
    ) -> ChatReply:
        """Envía un mensaje: compacta si hace falta, planifica/ejecuta y guarda.

        ``record_text`` is what the conversation stores for this user turn when the
        execution goal carries an internal instruction (e.g. an explicit plan choice).
        The goal itself is always ``text``.
        """
        # What the user actually wrote is what the conversation stores (and what
        # decides the reply language); the execution goal may additionally carry an
        # internal instruction (plan choice).
        turn_text = text if record_text is None else record_text
        compaction = await self.maybe_compact(text)
        history = [turn.to_dict() for turn in self.recent()]
        # Idioma de los avisos/respuesta: manda el mensaje actual; si es ambiguo, el
        # turno de usuario más reciente (nunca el del asistente).
        language = detect_user_language(turn_text, history)

        # Las alternativas del modo plan se consumen en este turno. Si el mensaje
        # acepta la recomendada ("la recomendada pero crea el agente…"), se convierte
        # en una orden de ejecución concreta: no se vuelve a pedir una elección ni se
        # contesta desde el catálogo ni con charla genérica.
        pending = self._pending_plan
        self._pending_plan = None
        extra = dict(extra_metadata) if extra_metadata else {}
        recommended = getattr(pending, "recommended", None)
        if recommended is not None and _accepts_recommendation(text):
            text = f"{text.rstrip()}\n{_option_instruction(recommended)}".strip()
            extra["allow_plan_options"] = False
            extra["execute_choice"] = True
        self.add("user", turn_text)
        self._autoname(turn_text)

        metadata: dict = {
            "history": history,
            "conversation": True,
            # Los grants "durante esta sesión" se guardan por este id, no por tarea.
            "session_id": self.session_id,
        }
        # Las menciones (@archivo, @símbolo) viajan con la tarea para que el runner las
        # resuelva como contexto de los nodos, igual que el historial.
        from satellite.context.mentions import parse as parse_mentions

        menciones = parse_mentions(turn_text, self.project_root)
        # Una mención a una ruta de FUERA del proyecto exige aprobación puntual: se
        # pide aquí (que es donde hay puerto), se lee una sola vez si la dan y el
        # contenido viaja como dato no confiable. La autorización no se persiste.
        bloque_externo, informe_externo, notas = await self._resolve_external_mentions(
            menciones.external, approval, language
        )
        # Una mención ambigua no se adjunta (no se elige una al azar): se dice y se pide
        # la ruta completa, para que el usuario sepa que no se leyó nada.
        notas = list(notas)
        for amb in menciones.ambiguous:
            candidatas = ", ".join(amb.candidates[:8])
            notas.append(
                _lang_text(
                    language,
                    f"@{amb.value} es ambiguo: coincide con {candidatas}. "
                    "Escribe la ruta completa para adjuntarlo.",
                    f"@{amb.value} is ambiguous: it matches {candidatas}. "
                    "Write the full path to attach it.",
                )
            )
        # Un archivo sensible/excluido no se lee como contexto: se dice, no se ofrece.
        for valor in menciones.ignored:
            notas.append(
                _lang_text(
                    language,
                    f"No adjunté @{valor}: es un archivo sensible o excluido del "
                    "índice y no se lee como contexto.",
                    f"I did not attach @{valor}: it is a sensitive or index-excluded "
                    "file and is not read as context.",
                )
            )
        contexto = self._mention_report(menciones, informe_externo)
        if plan_mode:
            metadata["plan_mode"] = True
        if extra:
            metadata.update(extra)
        # Las claves de contexto las fija SÓLO este flujo, y el contenido externo va
        # envuelto como ``ApprovedExternalContext``: descartar las que vengan por
        # metadata libre impide que ``extra_metadata`` (u otra vía interna) inyecte
        # contexto externo y se salte la aprobación explícita.
        for clave in _RESERVED_CONTEXT_KEYS:
            metadata.pop(clave, None)
        if menciones.refs:
            metadata["context_refs"] = [ref.to_dict() for ref in menciones.refs]
        if menciones.missing:
            metadata["context_missing"] = list(menciones.missing)
        if bloque_externo:
            metadata["context_external"] = ApprovedExternalContext(bloque_externo)

        # Memoria persistente: siempre viaja con el prompt (acotada en el render).
        memoria = self._session_memory().strip()
        if memoria:
            metadata["session_memory"] = memoria

        task = Task(goal=text, project_root=self.project_root, metadata=metadata)
        services = getattr(self.orchestrator, "services", None)
        if services is not None and approval is not None:
            services.approval = approval

        try:
            result = await self.orchestrator.execute(task)
        except Exception as exc:  # noqa: BLE001 — la conversación no debe romperse
            text = _lang_text(
                language,
                f"No pude completarlo: {exc}",
                f"I couldn't complete it: {exc}",
            )
            self.add("assistant", text, status="failed")
            await self._record_turn_summary(
                turn_text, text, status="failed", language=language
            )
            return ChatReply(
                text=text,
                status="failed",
                error=str(exc),
                compaction=compaction.to_dict() if compaction else None,
                context=contexto,
            )

        # El índice persistente lo adjunta el orquestador en cada turno. Si un turno que
        # pregunta por el proyecto se queda sin ese contexto, se dice en vez de dejar que la
        # respuesta parezca basada en archivos que no se leyeron.
        if (
            _missing_project_context(task)
            and _is_explanatory(turn_text)
            and not _result_reports_missing_context(result)
        ):
            notas.append(reply_fallback("no_project_context", language))

        reply_text = reply_from_result(result)
        if notas:
            # El aviso va delante para que no se pierda: si una ruta externa se denegó,
            # el usuario tiene que saberlo aunque la respuesta sea correcta.
            reply_text = "\n\n".join([*notas, reply_text])
        diagram = _diagram_payload(result.output)
        self.add(
            "assistant",
            reply_text,
            task_id=result.task_id,
            status=result.status.value,
            diagram=diagram,
        )
        await self._record_turn_summary(
            turn_text,
            reply_text,
            task_id=result.task_id,
            status=result.status.value,
            language=language,
        )
        # Si este turno ofreció alternativas, quedan pendientes para el siguiente.
        self._pending_plan = self._plan_from_result(result)
        return ChatReply(
            text=reply_text,
            task_id=result.task_id,
            status=result.status.value,
            error=result.error,
            output=dict(result.output or {}),
            compaction=compaction.to_dict() if compaction else None,
            metrics=result.metrics.to_dict() if result.metrics else {},
            context=contexto,
            diagram=diagram,
        )

    def _mention_report(self, menciones, externo: list[dict]) -> dict:
        """Resumen de las menciones para la UI (mismo parseo que la TUI/escritorio)."""
        return {
            "refs": [{"value": ref.value, "kind": ref.kind.value} for ref in menciones.refs],
            "missing": list(menciones.missing),
            "ambiguous": [amb.to_dict() for amb in menciones.ambiguous],
            "ignored": list(menciones.ignored),
            "external": list(externo),
            "summary": menciones.summary(),
        }

    async def _resolve_external_mentions(
        self, externas, approval: ApprovalPort | None, language: str = "es"
    ) -> tuple[str, list[dict], list[str]]:
        """Pide aprobación por cada @mención de fuera y lee (una vez) lo autorizado.

        Devuelve ``(bloque, informe, notas)``:

        - ``bloque``: contenido externo ya leído, como DATO no confiable, para este turno.
        - ``informe``: estado por mención (para la UI).
        - ``notas``: avisos en texto que se anteponen a la respuesta (en el idioma del
          usuario; las rutas quedan literales).

        No se guarda ninguna autorización: vale para esta petición y se descarta, así
        que no se reutiliza en turnos siguientes ni entre peticiones concurrentes.
        """
        if not externas:
            return "", [], []
        from satellite.context.paths import canonical
        from satellite.core.context_models import ContextRef

        services = getattr(self.orchestrator, "services", None)
        engine = getattr(services, "context", None)
        root = canonical(self.project_root) or Path(self.project_root)
        if engine is None:
            informe = [
                {"value": em.value, "path": str(em.path), "status": "no_engine"}
                for em in externas
            ]
            notas = [
                _lang_text(
                    language,
                    f"No leí {em.path}: este runtime no tiene motor de contexto para "
                    "rutas externas.",
                    f"I did not read {em.path}: this runtime has no context engine for "
                    "external paths.",
                )
                for em in externas
            ]
            return "", informe, notas

        aprobadas: list = []
        informe: list[dict] = []
        notas: list[str] = []
        for em in externas:
            estado = await self._approve_external(em, approval, root)
            informe.append({"value": em.value, "path": str(em.path), "status": estado})
            if estado == "approved":
                aprobadas.append(em)
                notas.append(
                    _lang_text(
                        language,
                        f"Adjunté {em.path} (fuera del proyecto) solo para este turno.",
                        f"I attached {em.path} (outside the project) for this turn only.",
                    )
                )
            elif estado == "denied":
                notas.append(
                    _lang_text(
                        language,
                        f"No leí {em.path}: está fuera del proyecto y no se autorizó.",
                        f"I did not read {em.path}: it is outside the project and was "
                        "not authorized.",
                    )
                )
            else:  # no_port
                notas.append(
                    _lang_text(
                        language,
                        f"No leí {em.path}: está fuera del proyecto y esta interfaz no "
                        "puede pedir confirmación.",
                        f"I did not read {em.path}: it is outside the project and this "
                        "interface cannot ask for confirmation.",
                    )
                )

        bloque = ""
        if aprobadas:
            # Una sola lectura por petición: se resuelven todas las aprobadas de golpe y
            # el resolver confina cada ruta contra la raíz o contra la autorización puntual.
            refs = [ContextRef(kind=em.kind, value=str(em.path)) for em in aprobadas]
            bundle = await engine.resolve(
                refs, allowed_paths=tuple(em.path for em in aprobadas)
            )
            bloque = "\n\n".join(
                f"--- {section.ref.value} (fuera del proyecto, autorizado este turno) ---\n"
                f"{section.content}"
                for section in bundle.sections
            )
        return bloque, informe, notas

    async def _approve_external(self, mencion, approval: ApprovalPort | None, root) -> str:
        """Pide aprobación para UNA mención externa; devuelve approved/denied/no_port."""
        if approval is None:
            return "no_port"
        from uuid import uuid4

        from satellite.core.proposal import ApprovalStatus

        summary = (
            f"adjuntar {mencion.path} por una @mención "
            "(fuera del proyecto, solo este turno)"
        )
        decision = await approval.request(
            {"kind": "context_path", "path": str(mencion.path), "summary": summary},
            ApprovalContext(
                kind="context_path",
                # Id único por petición: dos turnos iguales no comparten decisión.
                proposal_id=f"ctxext-{uuid4().hex}",
                summary=summary,
                details={
                    "tool": "mention",
                    "path": str(mencion.path),
                    "project_root": str(root),
                    "requested": mencion.value,
                    "consequence": "lectura de contexto (solo este turno)",
                },
            ),
        )
        if decision is not None and decision.status is ApprovalStatus.APPROVED:
            return "approved"
        return "denied"

    @staticmethod
    def _plan_from_result(result) -> object | None:
        """Las ``PlanOptions`` que trae el resultado, si ofreció alternativas."""
        output = result.output or {}
        if output.get("mode") != "plan_options":
            return None
        payload = output.get("plan_options")
        if not isinstance(payload, dict):
            return None
        try:
            from satellite.core.plan import PlanOptions

            return PlanOptions.from_dict(payload)
        except Exception:  # noqa: BLE001 — datos incompletos: se ignora, no se inventa
            return None

    async def choose(self, option, *, approval: ApprovalPort | None = None) -> ChatReply:
        """Elige una alternativa del modo plan y continúa con ella."""
        # La elección explícita consume las alternativas pendientes.
        self._pending_plan = None
        # The conversation stores the user-visible choice, not the internal English
        # instruction (which would otherwise skew later language detection).
        record = str(getattr(option, "title", "") or "").strip() or _option_instruction(
            option
        )
        return await self.send(
            _option_instruction(option),
            approval=approval,
            # execute_choice: es una orden de ejecución, no una pregunta, así que el
            # orquestador no debe atajarla con el catálogo ni con respuesta directa.
            extra_metadata={"allow_plan_options": False, "execute_choice": True},
            record_text=record,
        )


def _missing_project_context(task: Task) -> bool:
    """True si el orquestador no pudo adjuntar contexto de proyecto en este turno.

    El propio orquestador deja ``project_context_status`` cuando arma el turno; un
    orquestador que no lo haga (dobles de test, runtimes de terceros) no dispara el aviso.
    """
    return str(task.metadata.get("project_context_status") or "") in ("empty", "unavailable")


def _result_reports_missing_context(result: TaskResult) -> bool:
    """True si el orquestador ya avisó de la falta de contexto en este turno.

    Un overview sin índice lo responde el propio orquestador con ese aviso (sin llamar
    al modelo); para no repetirlo, la conversación no lo vuelve a anteponer.
    """
    output = result.output or {}
    return bool(output.get("project_context_missing"))


def _is_explanatory(goal: str) -> bool:
    """True si el goal pregunta por el proyecto (overview/README), no por una acción."""
    from satellite.context.project_index import explain_goal
    from satellite.orchestration.chat_detector import is_project_overview

    return explain_goal(goal) or is_project_overview(goal)


_OUTPUT_TEXT_KEYS = ("content", "answer", "text", "summary", "result", "output")
# Claves de metadatos que no forman parte de la respuesta visible.
_META_KEYS = ("user_language",)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def readable_output(output: dict | None, *, limit: int | None = None) -> str:
    """El texto legible de un output de nodo, sea cual sea su forma.

    Un nodo con ``output_schema`` devuelve las claves que dicte el schema (p.ej.
    ``skill``/``result``), así que mirar sólo ``content`` dejaba al usuario sin
    respuesta. Como último recurso se serializa el JSON: mejor eso que el silencio.
    Los metadatos internos (p.ej. ``user_language``) no se muestran.

    ``limit`` es opcional y sólo se usa para previews. El texto final del
    asistente NO se recorta: hacerlo cortaba respuestas largas a mitad de frase
    justo antes de guardarlas en la conversación.
    """
    def clip(text: str) -> str:
        return _clip(text, limit) if limit is not None else text

    if not isinstance(output, dict) or not output:
        return ""
    for key in _OUTPUT_TEXT_KEYS:
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return clip(value.strip())
        if isinstance(value, (list, tuple)) and value and all(
            isinstance(item, str) for item in value
        ):
            return clip(" ".join(str(item) for item in value))
    data = {key: value for key, value in output.items() if key not in _META_KEYS}
    if not data:
        return ""
    try:
        import json

        rendered = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - un dict simple siempre serializa
        return ""
    return clip(rendered) if rendered not in ("", "{}", "null") else ""


def _reply_language(output: dict) -> str:
    """Idioma del usuario para el fallback visible (``"en"`` o ``"es"`` por defecto)."""
    return "en" if str((output or {}).get("user_language") or "") == "en" else "es"


def reply_from_result(result: TaskResult) -> str:
    """Convierte el ``TaskResult`` en el mensaje que ve el usuario.

    Prefiere la **prosa localizada** que ya trae el resultado (``output['content']``, la
    que redacta ``_answer_in_prose`` en el idioma del usuario) para errores, capability
    gaps y modo plan; sólo si no la hay cae a un texto determinista en el idioma
    detectado (``output['user_language']``).
    """
    output = result.output or {}
    mode = output.get("mode")
    language = _reply_language(output)
    content = output.get("content")
    prose = content.strip() if isinstance(content, str) and content.strip() else ""
    if mode == "capability_gap":
        if prose:
            return prose
        gap = output.get("capability_gap") or {}
        missing = gap.get("missing", "")
        mensaje = reply_fallback("gap", language).format(missing=missing)
        opciones = output.get("options") or []
        if opciones:
            etiqueta = reply_fallback("options", language)
            mensaje += f"\n{etiqueta}: " + "; ".join(str(o) for o in opciones) + "."
        return mensaje
    if mode == "direct_reply":
        return str(content or result.summary or "")
    if mode == "needs_clarification":
        return str(output.get("question") or result.summary or "")
    if mode == "plan_options":
        if prose:
            return prose
        options = output.get("plan_options") or {}
        count = len(options.get("options") or [])
        return reply_fallback("alternatives", language).format(n=count)
    if result.error:
        if prose:
            return prose
        return f"{reply_fallback('error', language)}: {result.error}"
    content_text = readable_output(output)
    if content_text:
        # Camino del grafo: el texto del último nodo es la respuesta, tenga `content`
        # o las claves que dicte su `output_schema`.
        return content_text
    return result.summary or reply_fallback("no_answer", language)
