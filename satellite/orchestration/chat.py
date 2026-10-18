"""Conversación multi-turno sobre el orquestador.

Reutilizable por TUI/CLI/Web/MCP: mantiene el historial, lo resume para el
planner (como DATO no confiable) y ejecuta cada mensaje con el runtime, de modo
que un seguimiento ("agrega esto") se entienda en el contexto de lo anterior.
"""

from __future__ import annotations

import json
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
from satellite.core.task import Task, TaskResult
from satellite.retrieval.sanitize import sanitize

MAX_RECAP_TURNS = 8
MAX_TURN_CHARS = 1200
CONVERSATION_FILE = "conversation.json"
# Claves de contexto que sólo fija el flujo de chat (tras aprobación); la metadata libre
# no puede aportarlas.
_RESERVED_CONTEXT_KEYS = ("context_refs", "context_missing", "context_external")

_ROLE_LABEL = {"user": "usuario", "assistant": "asistente"}


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
    # Métricas de la ejecución (tokens/duración) para el pie de la respuesta en la TUI.
    metrics: dict = field(default_factory=dict)
    # @menciones del mensaje: qué se adjuntó, qué está fuera y qué no existía (para la UI).
    context: dict = field(default_factory=dict)


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
        # Las menciones (@archivo, @símbolo) viajan con la tarea para que el runner las
        # resuelva como contexto de los nodos, igual que el historial.
        from satellite.context.mentions import parse as parse_mentions

        menciones = parse_mentions(text, self.project_root)
        # Una mención a una ruta de FUERA del proyecto exige aprobación puntual: se
        # pide aquí (que es donde hay puerto), se lee una sola vez si la dan y el
        # contenido viaja como dato no confiable. La autorización no se persiste.
        bloque_externo, informe_externo, notas = await self._resolve_external_mentions(
            menciones.external, approval
        )
        # Una mención ambigua no se adjunta (no se elige una al azar): se dice y se pide
        # la ruta completa, para que el usuario sepa que no se leyó nada.
        notas = list(notas)
        for amb in menciones.ambiguous:
            candidatas = ", ".join(amb.candidates[:8])
            notas.append(
                f"@{amb.value} es ambiguo: coincide con {candidatas}. "
                "Escribe la ruta completa para adjuntarlo."
            )
        # Un archivo sensible/excluido no se lee como contexto: se dice, no se ofrece.
        for valor in menciones.ignored:
            notas.append(
                f"No adjunté @{valor}: es un archivo sensible o excluido del índice "
                "y no se lee como contexto."
            )
        contexto = self._mention_report(menciones, informe_externo)
        if plan_mode:
            metadata["plan_mode"] = True
        if extra_metadata:
            metadata.update(extra_metadata)
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
                context=contexto,
            )

        # El índice persistente lo adjunta el orquestador en cada turno. Si un turno que
        # pregunta por el proyecto se queda sin ese contexto, se dice en vez de dejar que la
        # respuesta parezca basada en archivos que no se leyeron.
        if _missing_project_context(task) and _is_explanatory(text):
            notas.append(
                "No hay contexto de proyecto que adjuntar "
                "(el índice está vacío o no está disponible)."
            )

        reply_text = reply_from_result(result)
        if notas:
            # El aviso va delante para que no se pierda: si una ruta externa se denegó,
            # el usuario tiene que saberlo aunque la respuesta sea correcta.
            reply_text = "\n\n".join([*notas, reply_text])
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
            metrics=result.metrics.to_dict() if result.metrics else {},
            context=contexto,
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
        self, externas, approval: ApprovalPort | None
    ) -> tuple[str, list[dict], list[str]]:
        """Pide aprobación por cada @mención de fuera y lee (una vez) lo autorizado.

        Devuelve ``(bloque, informe, notas)``:

        - ``bloque``: contenido externo ya leído, como DATO no confiable, para este turno.
        - ``informe``: estado por mención (para la UI).
        - ``notas``: avisos en texto que se anteponen a la respuesta.

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
                f"No leí {em.path}: este runtime no tiene motor de contexto para "
                "rutas externas." for em in externas
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
                    f"Adjunté {em.path} (fuera del proyecto) solo para este turno."
                )
            elif estado == "denied":
                notas.append(
                    f"No leí {em.path}: está fuera del proyecto y no se autorizó."
                )
            else:  # no_port
                notas.append(
                    f"No leí {em.path}: está fuera del proyecto y esta interfaz no "
                    "puede pedir confirmación."
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


def _missing_project_context(task: Task) -> bool:
    """True si el orquestador no pudo adjuntar contexto de proyecto en este turno.

    El propio orquestador deja ``project_context_status`` cuando arma el turno; un
    orquestador que no lo haga (dobles de test, runtimes de terceros) no dispara el aviso.
    """
    return str(task.metadata.get("project_context_status") or "") in ("empty", "unavailable")


def _is_explanatory(goal: str) -> bool:
    """True si el goal pregunta por el proyecto (overview/README), no por una acción."""
    from satellite.context.project_index import explain_goal

    return explain_goal(goal)


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
