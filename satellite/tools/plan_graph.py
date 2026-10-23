"""Tool `plan_graph`: el piloto emite un grafo de delegaciones en vez de delegar una a una.

Cuando el trabajo tiene partes **independientes** (que pueden correr a la vez) o
**dependientes** (una espera a otras), el piloto describe un DAG de nodos —cada uno delega
en un agente del catálogo— y este tool lo ejecuta en olas paralelas con ``GraphExecutor``.
Para un solo paso se sigue usando ``delegate`` (más simple y fluido): el piloto decide la
forma (docs/PLAN-grafo-piloto.md, fase 2).
"""

from __future__ import annotations

from satellite.core.tool_models import ToolResult
from satellite.orchestration.graph import RUN_IF, ExecutionGraph, GraphError
from satellite.orchestration.graph_executor import GraphExecutor
from satellite.tools.base import Tool, ToolContext
from satellite.tools.delegate import _clipped, _language_mismatch, _progress


class PlanGraphTool(Tool):
    """Ejecuta un grafo de delegaciones (nodos con dependencias) en paralelo."""

    @property
    def id(self) -> str:
        return "plan_graph"

    @property
    def description(self) -> str:
        return (
            "Runs several delegations as a graph (DAG), in parallel where possible. Use "
            "it only when the work has three or more independent parts (they run at the "
            "same time) or a real dependency structure (a node waits for others, e.g. an "
            "auditor after several coders). The graph has a fixed overhead that pays off "
            "only with that much parallel work: for one or two parts use delegate instead "
            "(one call per part) — it is cheaper and no slower. Each node delegates to an "
            "agent in your can_call list. Node fields: `id`, `agent_id`, `task` (goal + "
            "params, like delegate), `depends_on` (ids of nodes that must finish first), "
            "`run_if` ('all_ok' to run only if its dependencies succeeded — a verification "
            "gate — or 'any_done' to run regardless), `max_retries`. Returns each node's "
            "result."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("delegation",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "description": "Graph nodes; each delegates to one agent.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "agent_id": {"type": "string"},
                            "task": {"type": "object"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "run_if": {"type": "string", "enum": list(RUN_IF)},
                            "max_retries": {"type": "integer"},
                        },
                        "required": ["id", "agent_id"],
                    },
                }
            },
            "required": ["nodes"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        manager = getattr(tool_ctx, "delegation", None)
        ctx = getattr(tool_ctx, "delegation_ctx", None)
        if manager is None or ctx is None:
            return ToolResult(success=False, error="delegación no disponible en este contexto")
        raw_nodes = (arguments or {}).get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            return ToolResult(success=False, error="se requiere 'nodes' (una lista no vacía)")
        try:
            graph = ExecutionGraph.from_dict({"nodes": raw_nodes})
        except GraphError as exc:
            # El grafo mal formado es un error que el modelo puede corregir, no un crash.
            return ToolResult(success=False, error=f"grafo inválido: {exc}", error_code="invalid_graph")

        # Mismo filtro que `delegate`: un coder de otro lenguaje gasta su presupuesto en
        # un trabajo que no es el suyo. Se rechaza el grafo entero señalando el nodo.
        for node in graph.nodes:
            mismatch = _language_mismatch(node.agent_id, node.task)
            if mismatch:
                return ToolResult(
                    success=False,
                    error=f"nodo '{node.id}': {mismatch}",
                    error_code="wrong_language_agent",
                )

        # Checkpoint por forma del grafo: si un intento anterior se cortó, los nodos ya
        # hechos se saltan; al terminar bien, se limpia.
        from satellite.orchestration.graph_checkpoint import GraphCheckpoint, graph_key

        store = GraphCheckpoint(tool_ctx.project_root)
        key = graph_key(graph.to_dict())
        resume = store.load(key)
        executor = GraphExecutor(manager, sink=getattr(tool_ctx, "sink", None))
        run_result = await executor.run(
            graph,
            parent_ctx=ctx,
            project_root=tool_ctx.project_root,
            caller_spec=getattr(tool_ctx, "active_spec", None),
            approval=getattr(tool_ctx, "approval", None),
            cancel=getattr(tool_ctx, "cancel", None),
            resume=resume,
            checkpoint=lambda node_id, outcome: store.save_node(key, node_id, outcome),
        )
        if run_result.ok and not run_result.cancelled:
            store.clear(key)  # grafo completo: no hay nada que reanudar

        state = getattr(tool_ctx, "task_state", None)
        if isinstance(state, dict):
            # Cada nodo se registra como una delegación: así la traza del turno y la
            # respuesta (p. ej. un diagrama renderizado) ven lo que produjo el grafo.
            for node in graph.nodes:
                outcome = run_result.outcomes.get(node.id)
                if outcome is None:
                    continue
                result = outcome.result
                state.setdefault("delegations", []).append(
                    {
                        "agent_id": outcome.agent_id,
                        "ok": outcome.ok,
                        "status": outcome.status,
                        "output": dict(result.output or {}) if result is not None else {},
                        "error": outcome.error,
                        "tokens_in": outcome.tokens_in,
                        "tokens_out": outcome.tokens_out,
                        "budget_exhausted": bool(getattr(result, "budget_exhausted", False)),
                        "files_touched": list(outcome.files_touched),
                        "node": node.id,
                    }
                )
            state["graph"] = run_result.trace()

        nodes_out = []
        for node in graph.nodes:
            outcome = run_result.outcomes.get(node.id)
            if outcome is None:
                nodes_out.append({"id": node.id, "status": "not_run"})
                continue
            entry = {
                "id": node.id,
                "agent_id": outcome.agent_id,
                "status": outcome.status,
                "summary": outcome.summary,
            }
            if outcome.result is not None:
                entry["details"] = _clipped(outcome.result.output)
                entry.update(_progress(outcome.result))
            if outcome.skipped:
                entry["skipped_reason"] = outcome.error
            nodes_out.append(entry)

        if run_result.cancelled:
            return ToolResult(success=False, error="grafo cancelado", output={"nodes": nodes_out})
        return ToolResult(
            success=run_result.ok,
            error=None if run_result.ok else "uno o más nodos del grafo no terminaron bien",
            output={"ok": run_result.ok, "nodes": nodes_out},
        )


__all__ = ["PlanGraphTool"]
