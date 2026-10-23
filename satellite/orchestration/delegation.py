"""Delegación entre agentes (DAG): presupuesto, ciclos, permisos y trazabilidad.

La delegación es directa (no pasa por el orquestador): un agente con ``can_call``
invoca la tool ``delegate`` y el gestor crea el sub-agente con contexto limpio,
aplica presupuesto de profundidad/tokens, detecta ciclos y exige aprobación si
el hijo tendría más permisos que el padre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from satellite.agents.base import AgentContext, AgentFactory
from satellite.approval.base import ApprovalContext, ApprovalPort
from satellite.core.context_models import ContextBundle
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.result import AgentStatus
from satellite.core.spec import AgentSpec
from satellite.events.base import EventSink
from satellite.kernel.manifest import RESERVED_AGENT_IDS

SYSTEM_TASK_ID = "system"
# Agentes que conducen un turno completo (ver docs/PLAN-flujo-piloto.md, D1).
SESSION_ROOT_AGENT_IDS: frozenset[str] = frozenset({"pilot"})
# Nunca son destino de una delegación: el piloto conduce el turno (no se anida) y
# crear agentes es la tool ``create_agent``, que sí registra el agente.
NON_DELEGABLE_IDS: frozenset[str] = frozenset({"pilot", "agent_creator", "orchestrator"})
DEFAULT_MAX_DEPTH = 3
# Presupuesto de los agentes delegados: rondas de tools (tope; nunca más que su
# ``max_turns``) y resultados de tools recientes que se conservan enteros. Cada llamada
# reenvía toda la conversación, así que sin tope el coste crece con cada lectura.
DELEGATE_TOOL_ROUND_BUDGET = 20
DELEGATE_KEEP_TOOL_RESULTS = 12


class TokenLedger:
    """Tokens gastados por los delegados de un turno (compartido por toda la cadena)."""

    def __init__(self) -> None:
        self.spent = 0


@dataclass(frozen=True)
class DelegationContext:
    """Presupuesto y traza de una cadena de delegación."""

    task_id: str = ""
    depth: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH
    chain: tuple[str, ...] = ()
    token_budget: int | None = None
    tokens_used: int = 0
    user_language: str = ""
    # Sesión (conversación) del turno: los permisos concedidos «durante esta sesión»
    # (p. ej. control del escritorio) valen igual para los delegados.
    session_id: str = ""
    # Contador compartido por la raíz y todos sus descendientes: lo que gasta un
    # delegado descuenta del presupuesto de todo el turno, no sólo de su rama.
    ledger: TokenLedger | None = field(default=None, compare=False, repr=False)

    def child(
        self, agent_id: str, *, tokens_used: int = 0
    ) -> "DelegationContext":
        """Contexto del sub-agente (un nivel más profundo, con el id en la cadena)."""
        return DelegationContext(
            task_id=self.task_id,
            depth=self.depth + 1,
            max_depth=self.max_depth,
            chain=self.chain + (agent_id,),
            token_budget=self.token_budget,
            tokens_used=self.tokens_used + tokens_used,
            user_language=self.user_language,
            session_id=self.session_id,
            ledger=self.ledger,
        )

    def spend(self, tokens: int) -> None:
        """Descuenta del presupuesto del turno lo que gastó un delegado."""
        if self.ledger is not None and tokens > 0:
            self.ledger.spent += int(tokens)

    def remaining_tokens(self) -> int | None:
        """Tokens restantes del presupuesto (None si no hay presupuesto)."""
        if self.token_budget is None:
            return None
        spent = self.ledger.spent if self.ledger is not None else 0
        return max(0, self.token_budget - self.tokens_used - spent)

    def can_delegate(self, agent_id: str) -> tuple[bool, str]:
        """True si se puede delegar en ``agent_id`` según presupuesto y ciclos."""
        if self.depth + 1 > self.max_depth:
            return False, f"delegation depth limit ({self.max_depth}) reached"
        if agent_id in self.chain:
            return False, f"delegation cycle detected toward '{agent_id}'"
        if self.token_budget is not None and self.remaining_tokens() <= 0:
            return False, "delegation token budget exhausted"
        return True, ""


@dataclass(frozen=True)
class DelegationResult:
    """Resultado de una delegación (sólo el resumen, nunca el historial)."""

    ok: bool
    agent_id: str
    summary: str = ""
    status: str = ""
    output: dict = field(default_factory=dict)
    error: str | None = None
    tokens_used: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    # Se quedó sin rondas de tools: el trabajo puede estar a medias.
    budget_exhausted: bool = False
    # Rutas que el agente escribió o editó con éxito (lo ya hecho, para continuar).
    files_touched: tuple[str, ...] = ()
    # Rangos que leyó (``ruta:inicio-fin``): la continuación no explora desde cero.
    files_read: tuple[str, ...] = ()


WRITE_TOOLS = frozenset({"write_file", "edit_file"})


class DelegationManager:
    """Ejecuta delegaciones directas entre agentes con control determinista."""

    def __init__(
        self,
        factory: AgentFactory,
        *,
        registry=None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        token_budget: int | None = None,
        sink: EventSink | None = None,
        approval: ApprovalPort | None = None,
        require_can_call: bool = True,
    ) -> None:
        self.factory = factory
        self.registry = registry
        self.max_depth = max_depth
        self.token_budget = token_budget
        self.sink = sink
        self.approval = approval
        self.require_can_call = require_can_call

    def root_context(self, task_id: str = "") -> DelegationContext:
        """Contexto raíz de delegación (un agente de nivel superior)."""
        return DelegationContext(
            task_id=task_id,
            depth=0,
            max_depth=self.max_depth,
            token_budget=self.token_budget,
            ledger=TokenLedger(),
        )

    async def delegate(
        self,
        *,
        agent_id: str,
        task: dict | None,
        parent_ctx: DelegationContext,
        project_root: Path,
        caller_spec: AgentSpec | None = None,
        approval: ApprovalPort | None = None,
        cancel: object | None = None,
    ) -> DelegationResult:
        """Delega en ``agent_id`` con contexto limpio y devuelve su resumen."""
        # Las aprobaciones de esta delegación van por el puerto del turno (la tarjeta del
        # escritorio, el modal de la TUI); el global sólo si el turno no trae ninguno.
        port = approval if approval is not None else self.approval
        if agent_id in NON_DELEGABLE_IDS:
            # Quitarlos del catálogo que ve el modelo no basta: la lista decide lo que se
            # ejecuta, igual que con las tools.
            reason = (
                "agent_creator is not a delegation target: use the create_agent tool"
                if agent_id == "agent_creator"
                else f"'{agent_id}' is not a delegation target"
            )
            self._emit(EventType.POLICY_VIOLATION, parent_ctx, {"agent_id": agent_id, "reason": reason})
            return DelegationResult(ok=False, agent_id=agent_id, error=reason)
        allowed, reason = parent_ctx.can_delegate(agent_id)
        if not allowed:
            self._emit(EventType.POLICY_VIOLATION, parent_ctx, {"agent_id": agent_id, "reason": reason})
            return DelegationResult(ok=False, agent_id=agent_id, error=reason)

        callee_spec = self._spec_of(agent_id)
        # El piloto es la raíz de la sesión: su techo de permisos es la política de la
        # sesión (zonas de escritura, sandbox, modo ask/auto/unrestricted), no su lista de
        # tools. Delegar en un agente base no pide aprobación; un agente creado por el
        # usuario sí, como siempre.
        session_root = (
            caller_spec is not None
            and caller_spec.id in SESSION_ROOT_AGENT_IDS
            and agent_id in RESERVED_AGENT_IDS
        )
        if self.require_can_call and caller_spec is not None and not session_root:
            if not self._declared(caller_spec, agent_id):
                approved = await self._approve(
                    parent_ctx,
                    port,
                    kind="delegation",
                    summary=(
                        f"añadir '{agent_id}' a can_call de '{caller_spec.id}' "
                        "(no estaba declarado)"
                    ),
                )
                if not approved:
                    self._emit(
                        EventType.POLICY_VIOLATION,
                        parent_ctx,
                        {"agent_id": agent_id, "reason": "can_call no autorizado"},
                    )
                    return DelegationResult(
                        ok=False,
                        agent_id=agent_id,
                        error=f"'{agent_id}' is not in can_call and adding it was not approved",
                    )

        if (
            caller_spec is not None
            and not session_root
            and self._requires_escalation(caller_spec, callee_spec)
        ):
            approved = await self._approve(
                parent_ctx,
                port,
                kind="permission_escalation",
                summary=(
                    f"permitir que '{caller_spec.id}' delegue en '{agent_id}', que "
                    "tiene más tools que el padre"
                ),
            )
            if not approved:
                self._emit(
                    EventType.POLICY_VIOLATION,
                    parent_ctx,
                    {"agent_id": agent_id, "reason": "escalado de permisos denegado"},
                )
                return DelegationResult(
                    ok=False,
                    agent_id=agent_id,
                    error=f"permission escalation denied toward '{agent_id}'",
                )

        payload = dict(task or {})
        payload.setdefault("node_id", agent_id)
        agent = self.factory.create(agent_id)
        _apply_budget(agent)
        if hasattr(agent, "delegation_manager"):
            setattr(agent, "delegation_manager", self)
            setattr(agent, "delegation_ctx", parent_ctx.child(agent_id))

        self._emit(EventType.DELEGATION_STARTED, parent_ctx, {"agent_id": agent_id, "depth": parent_ctx.depth + 1})
        result = await agent.execute(
            payload,
            AgentContext(
                context=await self._project_rules(project_root),
                feedback=(),
                sink=self.sink,
                # A directly-created agent may not have a task id yet; the child must
                # still emit valid events (empty ids are rejected by ExecutionEvent).
                task_id=parent_ctx.task_id or SYSTEM_TASK_ID,
                session_id=getattr(parent_ctx, "session_id", ""),
                node_id=agent_id,
                project_root=project_root,
                user_language=parent_ctx.user_language,
                # The child runs inside the same session: without these it cannot ask
                # for an out-of-project path/command and cannot be cancelled.
                approval=port,
                cancel=cancel,
            ),
        )
        tokens_used = result.metrics.tokens_in + result.metrics.tokens_out
        parent_ctx.spend(tokens_used)
        summary = result.summary or str(result.output.get("summary", ""))
        self._emit(
            EventType.DELEGATION_COMPLETED,
            parent_ctx,
            {"agent_id": agent_id, "status": result.status.value, "tokens": tokens_used},
        )
        return DelegationResult(
            ok=result.status is AgentStatus.OK,
            agent_id=agent_id,
            summary=summary,
            status=result.status.value,
            output=dict(result.output),
            error=result.error,
            tokens_used=tokens_used,
            tokens_in=result.metrics.tokens_in,
            tokens_out=result.metrics.tokens_out,
            budget_exhausted=getattr(agent, "budget_exhausted", False) is True,
            files_touched=_files_touched(result.tool_calls),
            files_read=_files_read(result.tool_calls),
        )

    # --- helpers ---

    async def _project_rules(self, project_root: Path) -> ContextBundle:
        """Las instrucciones del proyecto (AGENTS.md) para el delegado.

        El delegado arranca sin la conversación, pero no sin las reglas del proyecto:
        sin esto dependía de que el piloto las copiara en la tarea.
        """
        from types import SimpleNamespace

        from satellite.context.instructions import instructions_ref

        engine = getattr(self.factory, "context_engine", None)
        refs = instructions_ref(SimpleNamespace(project_root=project_root))
        if engine is None or not refs:
            return ContextBundle()
        try:
            return await engine.resolve(refs)
        except Exception:  # noqa: BLE001 — sin reglas se delega igual
            return ContextBundle()

    def _spec_of(self, agent_id: str) -> AgentSpec | None:
        if self.registry is None:
            return None
        try:
            return self.registry.get(agent_id)
        except KeyError:
            return None

    @staticmethod
    def _declared(caller_spec: AgentSpec, agent_id: str) -> bool:
        return any(entry.agent_id == agent_id for entry in caller_spec.can_call)

    @staticmethod
    def _requires_escalation(caller_spec: AgentSpec, callee_spec: AgentSpec | None) -> bool:
        if callee_spec is None:
            return False
        return bool(set(callee_spec.tools) - set(caller_spec.tools))

    async def _approve(
        self,
        ctx: DelegationContext,
        port: ApprovalPort | None,
        *,
        kind: str,
        summary: str,
    ) -> bool:
        if port is None:
            # Sin puerto no se autoriza nada, pero el motivo real es que falta
            # configurarlo: se emite para que no parezca una negativa de política.
            self._emit(
                EventType.POLICY_VIOLATION,
                ctx,
                {
                    "kind": kind,
                    "summary": summary,
                    "reason": "aprobación no configurada: se deniega por defecto",
                    "denied": True,
                },
            )
            return False
        decision = await port.request(
            {"kind": kind, "summary": summary},
            ApprovalContext(
                kind=kind,
                task_id=ctx.task_id,
                summary=summary,
                details={"depth": ctx.depth + 1, "chain": " -> ".join(ctx.chain)},
            ),
        )
        return decision.approved

    def _emit(self, event_type: EventType, ctx: DelegationContext, payload: dict) -> None:
        if self.sink is None:
            return
        self.sink.emit(
            ExecutionEvent(
                event_type=event_type,
                task_id=ctx.task_id or SYSTEM_TASK_ID,
                payload=payload,
            )
        )


def _apply_budget(agent: object) -> None:
    """Aplica el presupuesto de delegado si el agente lo admite y no trae uno propio."""
    spec = getattr(agent, "spec", None)
    max_turns = getattr(spec, "max_turns", None)
    if getattr(agent, "tool_round_budget", 0) is None:
        budget = DELEGATE_TOOL_ROUND_BUDGET
        if isinstance(max_turns, int) and max_turns > 0:
            budget = min(budget, max_turns)
        setattr(agent, "tool_round_budget", budget)
    if getattr(agent, "keep_tool_results", 0) is None:
        setattr(agent, "keep_tool_results", DELEGATE_KEEP_TOOL_RESULTS)


MAX_FILES_READ = 30


def _files_read(records) -> tuple[str, ...]:
    """Rangos leídos con éxito (``ruta`` o ``ruta:inicio-fin``), sin repetir y acotados."""
    seen: dict[str, None] = {}
    for record in records or ():
        if getattr(record, "tool_name", "") != "read_file" or not record.success:
            continue
        args = record.arguments or {}
        path = args.get("path")
        if not isinstance(path, str) or not path:
            continue
        start, end = args.get("start_line"), args.get("end_line")
        label = f"{path}:{start or 1}-{end or 'end'}" if start or end else path
        seen.setdefault(label, None)
    return tuple(seen)[:MAX_FILES_READ]


def _files_touched(records) -> tuple[str, ...]:
    """Rutas escritas o editadas con éxito, en orden y sin repetir."""
    seen: dict[str, None] = {}
    for record in records or ():
        if getattr(record, "tool_name", "") not in WRITE_TOOLS or not record.success:
            continue
        path = (record.arguments or {}).get("path")
        if isinstance(path, str) and path:
            seen.setdefault(path, None)
    return tuple(seen)
