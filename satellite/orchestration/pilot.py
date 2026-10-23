"""PilotOrchestrator: un único agente con tools conduce cada turno.

El piloto lee el proyecto, decide, pregunta y delega en los microagentes del catálogo.
Sustituyó a la cascada de rutas deterministas y al planner JSON del modo antiguo
(docs/PLAN-flujo-piloto.md, fase 6).

Además del turno, el orquestador prepara el contexto (índice del proyecto, idiomas,
contenido externo aprobado), persiste el estado, atiende la cancelación y emite los
eventos ``TASK_STARTED``/``TASK_FINISHED`` que leen las superficies.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from satellite.agents.base import AgentContext
from satellite.core.context_models import ApprovedExternalContext, ContextBundle
from satellite.core.errors import ProviderError
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.language import (
    detect_user_language,
    is_standalone_greeting,
    reply_fallback,
)
from satellite.core.result import AgentStatus, ExecutionMetrics
from satellite.core.spec import CanCallEntry
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.events.base import EventSink
from satellite.orchestration.base import Orchestrator, RuntimeServices, approval_for
from satellite.orchestration.delegation import NON_DELEGABLE_IDS
from satellite.persistence.base import Store

PILOT_AGENT_ID = "pilot"
# Lo que no se ofrece como destino de delegación (y el gestor rechaza igualmente).
_NOT_DELEGABLE = NON_DELEGABLE_IDS

TOOL_ROUND_BUDGET = 12
KEEP_TOOL_RESULTS = 6

PLAN_INSTRUCTION = (
    "Plan mode: do not change anything and do not delegate. Investigate the project "
    "with your read-only tools, then call ask_user with 2 to 4 clearly different "
    "approaches as choices (label = short title, detail = steps, effort and risk) and "
    "set `recommended` when one is clearly better. If the user then picks one, the next "
    "turn will execute it."
)

_STATUS = {
    AgentStatus.OK: TaskStatus.OK,
    AgentStatus.FAILED: TaskStatus.FAILED,
    AgentStatus.NEEDS_USER_INPUT: TaskStatus.NEEDS_USER_INPUT,
    AgentStatus.CANCELLED: TaskStatus.CANCELLED,
    AgentStatus.SKIPPED: TaskStatus.FAILED,
}


class PilotOrchestrator(Orchestrator):
    """Conduce cada turno con el agente piloto.

    La TUI, la CLI, el escritorio y MCP leen ``services``, ``store``, ``sink``,
    ``expander`` y ``skill_runner`` directamente.
    """

    mode = "pilot"

    def __init__(
        self,
        services: RuntimeServices,
        *,
        store: Store | None = None,
        sink: EventSink | None = None,
        expander: object | None = None,
        skill_runner: object | None = None,
        precontext_mode: str = "map",
    ) -> None:
        self.services = services
        self.precontext_mode = precontext_mode
        self.store = store
        self.sink = sink if sink is not None else getattr(services, "sink", None)
        self.expander = expander
        self.skill_runner = skill_runner

    # ------------------------------------------------------------------ turno

    async def execute(self, task: Task) -> TaskResult:
        try:
            return await self._turn(task)
        except Exception as exc:  # noqa: BLE001 — todo turno termina con TASK_FINISHED
            # Un fallo inesperado (una tool, el contexto, la persistencia) no debe dejar
            # a las superficies esperando un cierre que nunca llega.
            language = detect_user_language(task.goal, task.metadata.get("history")) or "es"
            return await self._finish(task, self._failed(task, language, str(exc)))

    async def _turn(self, task: Task) -> TaskResult:
        services = self.services
        # El token se limpia ANTES de preparar el turno: una cancelación pedida mientras
        # se indexa el proyecto debe sobrevivir hasta que el agente la mire.
        cancel = getattr(services, "cancel", None)
        if cancel is not None:
            cancel.reset()
        await self.prepare_turn(task)
        history = task.metadata.get("history")
        language = detect_user_language(
            str(task.metadata.get("refined_from") or task.goal), history
        ) or "es"
        factory = getattr(services, "factory", None)
        can_create = getattr(factory, "can_create", None)
        if factory is None or (callable(can_create) and not can_create(PILOT_AGENT_ID)):
            # Sin el agente piloto (spec borrado, safe mode sin él) no hay quien
            # conduzca el turno: se dice claro en vez de improvisar otra ruta.
            error = "el agente 'pilot' no está disponible (revisa el catálogo con `satellite doctor`)"
            return await self._finish(task, self._failed(task, language, error))

        state: dict = {}
        plan_mode = bool(task.metadata.get("plan_mode"))
        try:
            agent = self._pilot(factory, services, plan_mode=plan_mode)
            ctx = AgentContext(
                context=await self._context(task, services),
                sink=self._sink(services),
                task_id=task.task_id,
                session_id=str(task.metadata.get("session_id") or ""),
                node_id=PILOT_AGENT_ID,
                project_root=task.project_root,
                user_language=language,
                approval=approval_for(task, services),
                cancel=cancel,
                task_state=state,
            )
            payload: dict = {"goal": task.goal, "node_id": PILOT_AGENT_ID}
            languages = str(task.metadata.get("project_languages") or "").strip()
            if languages:
                payload["project_languages"] = languages
            # Sin índice del proyecto lo dice el piloto (que puede comprobarlo con sus
            # tools), no una regla de la conversación sobre el texto del usuario.
            index_status = str(task.metadata.get("project_context_status") or "")
            if index_status in ("empty", "unavailable"):
                payload["project_index"] = index_status
            answering = task.metadata.get("answering")
            if isinstance(answering, dict):
                # El mensaje responde a la pregunta del turno anterior: se dice tal cual,
                # con la opción ya resuelta si el usuario eligió una por número o id.
                payload["answering"] = answering
            if plan_mode:
                payload["mode"] = "plan"
                payload["instruction"] = PLAN_INSTRUCTION
            timeout = float(getattr(agent.spec, "timeout_s", 0) or 0) or None
            agent_result = await asyncio.wait_for(agent.execute(payload, ctx), timeout)
        except asyncio.TimeoutError:
            result = self._failed(task, language, "el turno superó el tiempo máximo")
            return await self._finish(task, result)
        except ProviderError as exc:
            return await self._finish(task, self._failed(task, language, str(exc)))

        return await self._finish(
            task,
            self._result(task, agent_result, state, language),
            trace=turn_trace(agent_result, state),
        )

    async def prepare_turn(self, task: Task) -> None:
        """Arranca el turno: TASK_STARTED, persistencia y contexto del proyecto.

        Adjunta al turno el índice del proyecto, los idiomas detectados y el contenido
        externo aprobado para este turno.
        """
        # El contenido externo autorizado vale para ESTE turno: se saca de la metadata
        # antes de persistir la tarea, para no dejarlo guardado en disco. Se conserva el
        # objeto tal cual (no se convierte a texto) porque su tipo es la prueba de que
        # vino de una aprobación real.
        external_context = task.metadata.pop("context_external", None)
        self._emit(task, EventType.TASK_STARTED, {"goal": task.goal})
        if self.store is not None:
            await self.store.save_task(task)
        # El estado del índice viaja en la metadata para que la respuesta pueda decir,
        # sin fingir que se leyó algo, cuando no había índice ni archivos que indexar.
        try:
            from satellite.context.project_index import ProjectIndex

            context_engine = getattr(self.services, "context", None)
            index = getattr(getattr(context_engine, "resolver", None), "index", None)
            if index is None:
                index = ProjectIndex(task.project_root)
            index.ensure_persisted()
            task.metadata["project_context"] = index.context_for_goal(
                task.goal, mode=self._precontext_mode()
            )
            task.metadata["project_context_status"] = (
                "ok" if task.metadata["project_context"] else "empty"
            )
            # Los lenguajes salen del mismo índice: sin un segundo recorrido del proyecto.
            hint = index.language_hint()
            if hint:
                task.metadata["project_languages"] = hint
        except (OSError, ValueError, TypeError):
            # Un problema al indexar no debe impedir el turno.
            task.metadata.setdefault("project_context", "")
            task.metadata["project_context_status"] = "unavailable"
            task.metadata.pop("project_languages", None)
        append_external_context(task, external_context)

    def _precontext_mode(self) -> str:
        """``SATELLITE_PRECONTEXT`` (para comparar modos en la evaluación) o la config."""
        import os

        from satellite.context.project_index import PRECONTEXT_MODES

        override = os.environ.get("SATELLITE_PRECONTEXT", "").strip().lower()
        if override in PRECONTEXT_MODES:
            return override
        return self.precontext_mode if self.precontext_mode in PRECONTEXT_MODES else "map"

    # ----------------------------------------------------------------- piezas

    def _pilot(self, factory, services, *, plan_mode: bool = False):
        """Crea el piloto con el catálogo actual como destinos de delegación.

        En modo plan no puede delegar: investiga en solo lectura y propone enfoques.
        """
        agent = factory.create(PILOT_AGENT_ID)
        # Presupuesto de exploración: rondas de tools y resultados que se conservan
        # enteros. Sin él, una pregunta podía costar decenas de lecturas y millones de
        # tokens de entrada (evaluación del 2026-10-22).
        agent.tool_round_budget = TOOL_ROUND_BUDGET
        agent.keep_tool_results = KEEP_TOOL_RESULTS
        if plan_mode:
            agent.tools = [tool for tool in agent.tools if tool.id != "delegate"]
            agent.stream_text = True
            return agent
        registry = getattr(services, "registry", None)
        specs = sorted(registry.all(), key=lambda spec: spec.id) if registry else []
        entries = tuple(
            CanCallEntry(agent_id=spec.id, description=spec.description)
            for spec in specs
            if spec.id not in _NOT_DELEGABLE
        )
        agent.spec = replace(agent.spec, can_call=entries)
        agent.stream_text = True
        return agent

    async def _context(self, task: Task, services) -> ContextBundle:
        """Lo mismo que recibe un nodo del grafo: menciones, instrucciones e historial."""
        from satellite.context.instructions import instructions_ref
        from satellite.context.mentions import user_refs
        from satellite.orchestration.turn_context import with_conversation

        bundle = ContextBundle()
        refs = tuple(user_refs(task)) + tuple(instructions_ref(task))
        engine = getattr(services, "context", None)
        if engine is not None and refs:
            bundle = await engine.resolve(refs)
        return with_conversation(bundle, task)

    def _sink(self, services):
        return getattr(services, "sink", None) or self.sink

    def _result(
        self, task: Task, agent_result, state: dict, language: str
    ) -> TaskResult:
        from satellite.orchestration.chat import _diagram_payload

        output = dict(agent_result.output or {})
        content = str(output.get("content") or "").strip()
        status = _STATUS.get(agent_result.status, TaskStatus.FAILED)
        if not content and status is TaskStatus.OK:
            # Un «hola» con respuesta vacía se contesta con un saludo, no con un aviso.
            key = "greeting" if is_standalone_greeting(task.goal) else "empty_response"
            content = reply_fallback(key, language)
        elif not content and status is TaskStatus.CANCELLED:
            # Sin texto propio, la superficie volcaba el JSON del resultado.
            reason = str(output.get("reason") or "").strip()
            content = reply_fallback("cancelled", language)
            if reason:
                content = f"{content}: {reason}"
        reply: dict = {
            "mode": "pilot_reply",
            "content": content,
            "user_language": language,
        }
        if output.get("mode") == "question":
            reply["mode"] = "question"
            for key in ("question", "choices", "recommended"):
                reply[key] = output.get(key) or ([] if key == "choices" else "")
        # Un diagrama renderizado por un agente delegado viaja con la respuesta para
        # que la UI lo pinte (el piloto sólo lo describe en su texto).
        for delegation in reversed(state.get("delegations", [])):
            diagram = _diagram_payload(delegation.get("output"))
            if diagram:
                reply.update(diagram)
                reply["diagram_type"] = diagram.get("diagram_type", "")
                break
        reply["delegations"] = [
            {key: item.get(key) for key in ("agent_id", "ok", "status", "error")}
            for item in state.get("delegations", [])
        ]
        # El consumo del turno incluye el de los agentes en los que se delegó.
        metrics = agent_result.metrics
        delegated_in = sum(int(d.get("tokens_in") or 0) for d in state.get("delegations", []))
        delegated_out = sum(int(d.get("tokens_out") or 0) for d in state.get("delegations", []))
        if delegated_in or delegated_out:
            metrics = replace(
                metrics,
                tokens_in=metrics.tokens_in + delegated_in,
                tokens_out=metrics.tokens_out + delegated_out,
            )
        return TaskResult(
            task_id=task.task_id,
            status=status,
            summary=content or (agent_result.summary or ""),
            output=reply,
            findings=agent_result.findings,
            metrics=metrics,
            error=agent_result.error,
        )

    @staticmethod
    def _failed(task: Task, language: str, error: str) -> TaskResult:
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            summary=f"{reply_fallback('error', language)}: {error}",
            output={"mode": "pilot_reply", "user_language": language},
            metrics=ExecutionMetrics(),
            error=error,
        )

    async def _finish(
        self, task: Task, result: TaskResult, *, trace: dict | None = None
    ) -> TaskResult:
        """Persiste el estado (con la traza del turno) y emite ``TASK_FINISHED``."""
        if self.store is not None:
            state: dict = {"status": result.status.value}
            if trace is not None:
                state["trace"] = trace
            try:
                await self.store.save_state(task.task_id, state)
            except Exception:  # noqa: BLE001 — no persistir no debe ocultar la respuesta
                # ni dejar a las superficies sin TASK_FINISHED.
                pass
        self._emit(task, EventType.TASK_FINISHED, {"status": result.status.value})
        return result

    def _emit(self, task: Task, event_type: EventType, payload: dict) -> None:
        sink = self._sink(self.services)
        if sink is not None:
            sink.emit(
                ExecutionEvent(event_type=event_type, task_id=task.task_id, payload=payload)
            )

    def cancel(self, task_id: str = "", reason: str = "") -> dict:
        """Pide cancelar el turno en curso (cooperativa).

        El runtime ejecuta un turno cada vez, así que el id es informativo: se devuelve
        para que quien lo pidió sepa a qué se refería.
        """
        cancel = getattr(self.services, "cancel", None)
        if cancel is None:
            return {"cancelled": False, "reason": "este runtime no tiene cancelación"}
        cancel.request(reason or "cancelado")
        return {"cancelled": True, "task_id": task_id, "reason": cancel.reason}


def turn_trace(agent_result, state: dict) -> dict:
    """Lo que hizo el turno: tools del piloto y delegaciones (lo que antes era el grafo).

    Sólo ids, estados y contadores: ni argumentos completos ni salidas, que pueden
    llevar contenido del proyecto.
    """
    tools = [
        {"tool": record.tool_name, "ok": bool(record.success)}
        for record in getattr(agent_result, "tool_calls", ()) or ()
    ]
    delegations = [
        {
            "agent_id": item.get("agent_id"),
            "status": item.get("status"),
            "ok": bool(item.get("ok")),
            "tokens_in": int(item.get("tokens_in") or 0),
            "tokens_out": int(item.get("tokens_out") or 0),
            "files_touched": list(item.get("files_touched") or ()),
            "budget_exhausted": bool(item.get("budget_exhausted")),
            "error": item.get("error"),
        }
        for item in state.get("delegations", [])
    ]
    trace = {"pilot_tools": tools, "delegations": delegations}
    graph = state.get("graph")
    if graph:
        trace["graph"] = graph
    return trace


def append_external_context(task: Task, external: object = None) -> None:
    """Suma al ``project_context`` el contenido externo **ya autorizado** este turno.

    Sólo se acepta ``ApprovedExternalContext``, el envoltorio que crea ``ChatSession.send``
    tras pedir la aprobación puntual. Cualquier otro valor en ``metadata['context_external']``
    (un texto suelto, por ``extra_metadata`` u otra vía interna) se ignora: así la
    aprobación del usuario es la única fuente del contexto externo del turno.
    """
    value = task.metadata.get("context_external") if external is None else external
    if not isinstance(value, ApprovedExternalContext):
        return
    extra = value.text.strip()
    if not extra:
        return
    base = str(task.metadata.get("project_context") or "").strip()
    task.metadata["project_context"] = f"{base}\n\n{extra}" if base else extra
