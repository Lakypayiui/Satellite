"""GraphExecutor: ejecuta un ``ExecutionGraph`` por olas paralelas (fase 1).

Toma el grafo que emitió el piloto y lo corre sobre ``DelegationManager``: cada ola son
los nodos cuyas dependencias ya terminaron, lanzados a la vez; los dependientes esperan a
todas sus fuentes. Reutiliza el presupuesto de tokens compartido, la aprobación del turno
y la cancelación cooperativa que ya tiene la delegación (docs/PLAN-grafo-piloto.md).

No decide nada: el piloto decidió el grafo. Aquí sólo se ejecuta de forma determinista.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from satellite.core.events import EventType, ExecutionEvent
from satellite.orchestration.delegation import DelegationContext, DelegationResult
from satellite.orchestration.graph import ExecutionGraph, GraphNode

# Cuánto del resumen de una dependencia se pasa al nodo que la espera.
_UPSTREAM_SUMMARY_CHARS = 600


@dataclass
class NodeOutcome:
    """Cómo terminó un nodo del grafo."""

    id: str
    agent_id: str
    status: str  # ok | failed | skipped
    ok: bool = False
    skipped: bool = False
    attempts: int = 0
    continuations: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    files_touched: tuple[str, ...] = ()
    files_read: tuple[str, ...] = ()
    summary: str = ""
    error: str | None = None
    result: DelegationResult | None = None

    def to_trace(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "status": self.status,
            "ok": self.ok,
            "skipped": self.skipped,
            "attempts": self.attempts,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "files_touched": list(self.files_touched),
            "continuations": self.continuations,
            "error": self.error,
        }

    @classmethod
    def from_checkpoint(cls, data: dict) -> "NodeOutcome":
        """Reconstruye lo justo de un nodo ya hecho (para reanudar y para las puertas)."""
        return cls(
            id=str(data.get("id", "")),
            agent_id=str(data.get("agent_id", "")),
            status=str(data.get("status", "ok")),
            ok=bool(data.get("ok")),
            skipped=bool(data.get("skipped")),
            files_touched=tuple(data.get("files_touched") or ()),
            summary=str(data.get("summary", "")),
        )


@dataclass
class GraphRunResult:
    """Resultado de ejecutar el grafo entero."""

    outcomes: dict[str, NodeOutcome] = field(default_factory=dict)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """True si ningún nodo ejecutado falló (los omitidos no cuentan como fallo)."""
        run = [o for o in self.outcomes.values() if not o.skipped]
        return bool(run) and all(o.ok for o in run)

    @property
    def tokens_in(self) -> int:
        return sum(o.tokens_in for o in self.outcomes.values())

    @property
    def tokens_out(self) -> int:
        return sum(o.tokens_out for o in self.outcomes.values())

    def trace(self) -> list[dict]:
        return [self.outcomes[o].to_trace() for o in self.outcomes]


class GraphExecutor:
    """Ejecuta grafos de delegaciones en olas paralelas."""

    def __init__(self, manager, *, sink=None) -> None:
        self.manager = manager
        self.sink = sink

    async def run(
        self,
        graph: ExecutionGraph,
        *,
        parent_ctx: DelegationContext,
        project_root: Path,
        caller_spec=None,
        approval=None,
        cancel=None,
        resume: dict | None = None,
        checkpoint=None,
    ) -> GraphRunResult:
        """Ejecuta el grafo. ``resume``: nodos ya hechos (de un turno cortado), que se
        saltan. ``checkpoint(node_id, outcome_dict)``: se llama al terminar cada nodo,
        para que quien llama lo persista y pueda reanudar si el turno se corta."""
        graph.validate()
        result = GraphRunResult()
        done: set[str] = set()
        started: set[str] = set()
        # Reanudar: los nodos ya completados en un intento anterior no se repiten.
        for node_id, data in (resume or {}).items():
            if graph.node(node_id) is not None:
                result.outcomes[node_id] = NodeOutcome.from_checkpoint({**data, "id": node_id})
                done.add(node_id)
                started.add(node_id)
        retries_left = graph.limits.max_retries_total
        replans_left = graph.limits.max_replans
        self._emit(
            EventType.GRAPH_BUILT,
            parent_ctx,
            {"nodes": [{"id": n.id, "agent_id": n.agent_id, "depends_on": list(n.depends_on)} for n in graph.nodes]},
        )

        while True:
            if self._cancelled(cancel):
                result.cancelled = True
                return result
            wave = graph.ready(done, started)
            if not wave:
                break
            # Puerta ``run_if``: un nodo ``all_ok`` se omite si alguna dependencia no salió
            # bien (o se omitió); entonces no se ejecuta y se arrastra como omitido.
            runnable: list[GraphNode] = []
            for node in wave:
                if node.run_if == "all_ok" and not self._deps_ok(node, result):
                    result.outcomes[node.id] = NodeOutcome(
                        id=node.id, agent_id=node.agent_id, status="skipped", skipped=True,
                        error="una dependencia no terminó correctamente",
                    )
                    done.add(node.id)
                else:
                    runnable.append(node)
            if not runnable:
                continue
            for node in runnable:
                started.add(node.id)
            budget = {"retries": retries_left, "replans": replans_left}
            outcomes = await asyncio.gather(
                *(
                    self._run_node(
                        node, result, budget,
                        parent_ctx=parent_ctx, project_root=project_root,
                        caller_spec=caller_spec, approval=approval, cancel=cancel,
                    )
                    for node in runnable
                )
            )
            retries_left = budget["retries"]
            replans_left = budget["replans"]
            for outcome in outcomes:
                result.outcomes[outcome.id] = outcome
                done.add(outcome.id)
                if checkpoint is not None:
                    try:
                        checkpoint(outcome.id, outcome.to_trace())
                    except Exception:  # noqa: BLE001 — un checkpoint que falla no para el grafo
                        pass
        return result

    async def _run_node(
        self, node: GraphNode, result: GraphRunResult, budget: dict, *,
        parent_ctx, project_root, caller_spec, approval, cancel,
    ) -> NodeOutcome:
        task = self._task_with_upstream(node, result)
        outcome = NodeOutcome(id=node.id, agent_id=node.agent_id, status="failed")
        while True:
            outcome.attempts += 1
            delegation = await self.manager.delegate(
                agent_id=node.agent_id, task=task, parent_ctx=parent_ctx,
                project_root=project_root, caller_spec=caller_spec,
                approval=approval, cancel=cancel,
            )
            outcome.tokens_in += delegation.tokens_in
            outcome.tokens_out += delegation.tokens_out
            outcome.result = delegation
            outcome.summary = delegation.summary or ""
            outcome.files_touched = _merge(outcome.files_touched, getattr(delegation, "files_touched", ()))
            outcome.files_read = _merge(outcome.files_read, getattr(delegation, "files_read", ()))
            outcome.error = delegation.error
            if delegation.ok and not getattr(delegation, "budget_exhausted", False):
                outcome.ok = True
                outcome.status = "ok"
                return outcome
            if delegation.ok and getattr(delegation, "budget_exhausted", False):
                # Terminó a medias (agotó rondas): se continúa con lo ya hecho como
                # contexto, mientras quede presupuesto de replanes. No es un fallo.
                if budget["replans"] <= 0 or self._cancelled(cancel):
                    outcome.ok = True
                    outcome.status = "incomplete"
                    return outcome
                budget["replans"] -= 1
                outcome.continuations += 1
                task = self._continuation_task(task, outcome)
                continue
            # Falló: reintento mientras le quede a él y al presupuesto global.
            if outcome.attempts > node.max_retries or budget["retries"] <= 0 or self._cancelled(cancel):
                return outcome
            budget["retries"] -= 1

    @staticmethod
    def _continuation_task(task: dict, outcome: NodeOutcome) -> dict:
        """La misma tarea, con lo ya hecho/leído para que continúe sin empezar de cero."""
        out = dict(task)
        context = dict(out.get("context") or {}) if isinstance(out.get("context"), dict) else {}
        context["continuation"] = {
            "note": "Previous attempt ran out of rounds. Continue from here; do not redo "
            "what is already done.",
            "files_touched": list(outcome.files_touched),
            "files_read": list(outcome.files_read),
        }
        out["context"] = context
        return out

    def _task_with_upstream(self, node: GraphNode, result: GraphRunResult) -> dict:
        """La tarea del nodo, con un resumen de lo que hicieron sus dependencias."""
        task = dict(node.task or {})
        upstream = []
        for dep in node.depends_on:
            outcome = result.outcomes.get(dep)
            if outcome is None:
                continue
            upstream.append(
                {
                    "node": dep,
                    "agent_id": outcome.agent_id,
                    "status": outcome.status,
                    "files_touched": list(outcome.files_touched),
                    "summary": (outcome.summary or "")[:_UPSTREAM_SUMMARY_CHARS],
                }
            )
        if upstream:
            context = dict(task.get("context") or {}) if isinstance(task.get("context"), dict) else {}
            context["upstream"] = upstream
            task["context"] = context
        return task

    @staticmethod
    def _deps_ok(node: GraphNode, result: GraphRunResult) -> bool:
        return all(
            (result.outcomes.get(dep) is not None and result.outcomes[dep].ok)
            for dep in node.depends_on
        )

    @staticmethod
    def _cancelled(cancel) -> bool:
        return cancel is not None and getattr(cancel, "cancelled", False)

    def _emit(self, event_type: EventType, ctx: DelegationContext, payload: dict) -> None:
        if self.sink is None:
            return
        self.sink.emit(
            ExecutionEvent(
                event_type=event_type,
                task_id=getattr(ctx, "task_id", "") or "system",
                payload=payload,
            )
        )


def _merge(current: tuple[str, ...], extra) -> tuple[str, ...]:
    """Une rutas conservando el orden y sin repetir (lo tocado/leído entre intentos)."""
    seen: dict[str, None] = {key: None for key in current}
    for item in extra or ():
        if isinstance(item, str) and item:
            seen.setdefault(item, None)
    return tuple(seen)


__all__ = ["GraphExecutor", "GraphRunResult", "NodeOutcome"]
