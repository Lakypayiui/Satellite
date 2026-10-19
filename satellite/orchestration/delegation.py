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

SYSTEM_TASK_ID = "system"
DEFAULT_MAX_DEPTH = 3


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
        )

    def remaining_tokens(self) -> int | None:
        """Tokens restantes del presupuesto (None si no hay presupuesto)."""
        if self.token_budget is None:
            return None
        return max(0, self.token_budget - self.tokens_used)

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
            task_id=task_id, depth=0, max_depth=self.max_depth, token_budget=self.token_budget
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
        allowed, reason = parent_ctx.can_delegate(agent_id)
        if not allowed:
            self._emit(EventType.POLICY_VIOLATION, parent_ctx, {"agent_id": agent_id, "reason": reason})
            return DelegationResult(ok=False, agent_id=agent_id, error=reason)

        callee_spec = self._spec_of(agent_id)
        if self.require_can_call and caller_spec is not None:
            if not self._declared(caller_spec, agent_id):
                approved = await self._approve(
                    parent_ctx,
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

        if caller_spec is not None and self._requires_escalation(caller_spec, callee_spec):
            approved = await self._approve(
                parent_ctx,
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
        if hasattr(agent, "delegation_manager"):
            setattr(agent, "delegation_manager", self)
            setattr(agent, "delegation_ctx", parent_ctx.child(agent_id))

        self._emit(EventType.DELEGATION_STARTED, parent_ctx, {"agent_id": agent_id, "depth": parent_ctx.depth + 1})
        result = await agent.execute(
            payload,
            AgentContext(
                context=ContextBundle(),
                feedback=(),
                sink=self.sink,
                task_id=parent_ctx.task_id,
                node_id=agent_id,
                project_root=project_root,
                user_language=parent_ctx.user_language,
                # The child runs inside the same session: without these it cannot ask
                # for an out-of-project path/command and cannot be cancelled.
                approval=approval if approval is not None else self.approval,
                cancel=cancel,
            ),
        )
        tokens_used = result.metrics.tokens_in + result.metrics.tokens_out
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
        )

    # --- helpers ---

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

    async def _approve(self, ctx: DelegationContext, *, kind: str, summary: str) -> bool:
        if self.approval is None:
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
        decision = await self.approval.request(
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
