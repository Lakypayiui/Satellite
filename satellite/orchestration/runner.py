"""DefaultGraphRunner: ejecuta el ExecutionGraph con comunicación directa."""

from __future__ import annotations

import time
from typing import Any

from satellite.agents.base import AgentContext, AgentFactory
from satellite.core.context_models import ContextBundle, ContextRef
from satellite.core.errors import ContractError, GraphValidationError
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.graph import ExecutionGraph
from satellite.core.result import AgentResult, AgentStatus, ExecutionMetrics, Finding
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import GraphRunner, RuntimeServices
from satellite.events.base import EventSink
from satellite.observability.logger import ExecutionRecord


def _with_conversation(bundle: ContextBundle, task) -> ContextBundle:
    """Antepone la conversación reciente al contexto de un nodo.

    Va como sección **dinámica** (nunca al prefijo estable: tools → system → catálogo),
    así un seguimiento como «explícamela» tiene antecedente también cuando acaba
    ejecutando un grafo. Sin historial no se añade nada.
    """
    from satellite.core.context_models import ContextRef, ContextRefKind, ContextSection
    from satellite.orchestration.planner_prompt import render_history

    conversacion = render_history(task.metadata.get("history")).strip()
    if not conversacion:
        return bundle
    seccion = ContextSection(
        ref=ContextRef(ContextRefKind.CONVERSATION, "conversación reciente"),
        content=conversacion,
    )
    return ContextBundle(sections=(seccion,) + bundle.sections)


class DefaultGraphRunner(GraphRunner):
    """Ejecuta A→B→C directo, sin pasar por el Orchestrator."""

    def __init__(
        self,
        factory: AgentFactory,
        context_engine=None,
        sink: EventSink | None = None,
        logger=None,
    ) -> None:
        self.factory = factory
        self.context_engine = context_engine
        self.sink = sink
        self.logger = logger

    async def run(
        self,
        graph: ExecutionGraph,
        task: Task,
        services: RuntimeServices,
    ) -> TaskResult:
        """Ejecuta el grafo y devuelve el resultado agregado."""
        factory = services.factory or self.factory
        if factory is None:
            raise ContractError("DefaultGraphRunner: falta services.factory")
        engine = services.context or self.context_engine
        sink = services.sink or self.sink

        # 1. Validar grafo
        graph.validate()

        # 2. Emitir inicio
        self._emit(sink, task, EventType.TASK_STARTED, payload={"goal": task.goal})
        self._emit(
            sink,
            task,
            EventType.GRAPH_BUILT,
            payload={"nodes": len(graph.nodes), "edges": len(graph.edges)},
        )

        # 3. Estado
        visits: dict[str, int] = {}
        attempts: dict[str, int] = {}
        results: dict[str, AgentResult] = {}
        total_steps = 0
        tokens_total = 0
        started = time.monotonic()

        # 4. Frontera inicial
        entry = graph.entry_node
        initial_task = dict(graph.nodes[entry].task) if graph.nodes[entry].task else {"goal": task.goal}
        frontier: list[tuple[str, dict]] = [
            (entry, {"task": initial_task, "feedback": None, "context_refs": list(graph.nodes[entry].context_refs)})
        ]

        # 5. Bucle de trabajo
        while frontier:
            node_id, entrada = frontier.pop(0)
            node = graph.nodes[node_id]
            visits[node_id] = visits.get(node_id, 0) + 1
            total_steps += 1

            # Límites
            if total_steps > graph.limits.max_total_steps:
                return self._abort(
                    task, sink, node_id,
                    f"límite max_total_steps={graph.limits.max_total_steps} superado",
                    results, attempts, started,
                )
            if visits[node_id] > graph.limits.max_node_visits:
                return self._abort(
                    task, sink, node_id,
                    f"límite max_node_visits={graph.limits.max_node_visits} superado en '{node_id}'",
                    results, attempts, started,
                )

            self._emit(
                sink,
                task,
                EventType.NODE_STARTED,
                node_id=node_id,
                payload={"spec": node.spec_id},
            )

            # Contexto
            refs: list[ContextRef] | tuple[ContextRef, ...] = entrada.get("context_refs", [])
            bundle = ContextBundle()
            if engine is not None and refs:
                bundle = await engine.resolve(refs)
            bundle = _with_conversation(bundle, task)

            # Agente
            agent = factory.create(node.spec_id, model_role=node.model_role)
            entrada["task"].setdefault("node_id", node_id)
            agent_ctx = AgentContext(
                context=bundle,
                feedback=tuple(entrada.get("feedback") or ()),
                sink=sink,
                task_id=task.task_id,
                node_id=node_id,
                project_root=task.project_root,
                # El puerto de la sesión: sin esto, una tool que pide una ruta de
                # fuera del proyecto no tenía a quién preguntar.
                approval=getattr(services, "approval", None),
            )
            result = await agent.execute(entrada["task"], agent_ctx)
            results[node_id] = result

            # Observabilidad: registrar la ejecución del nodo
            if self.logger is not None:
                metrics = result.metrics
                self.logger.log(
                    ExecutionRecord(
                        provider="",
                        model=node.model_role or "",
                        agent_id=node.spec_id,
                        input=dict(entrada.get("task", {})),
                        output=dict(result.output),
                        duration_ms=float(metrics.duration_ms),
                        status=result.status.value,
                        error_message=result.error or "",
                        tokens_before=metrics.tokens_in,
                        tokens_after=metrics.tokens_out,
                        task_id=task.task_id,
                        findings_count=len(result.findings),
                    )
                )

            self._emit(
                sink,
                task,
                EventType.NODE_FINISHED,
                node_id=node_id,
                payload={"spec_id": node.spec_id, "status": result.status.value, "summary": result.summary},
            )

            tokens_total += result.metrics.tokens_in + result.metrics.tokens_out
            if (
                graph.limits.token_budget is not None
                and tokens_total > graph.limits.token_budget
            ):
                return self._abort(
                    task, sink, node_id,
                    f"límite token_budget={graph.limits.token_budget} superado",
                    results, attempts, started,
                )

            # Sucesores
            edges = graph.successors(node_id, result)
            if not edges:
                if len(frontier) == 0 and result.status is AgentStatus.OK:
                    break  # fin normal
                return self._abort(
                    task, sink, node_id,
                    f"dead-end: nodo '{node_id}' sin salida para estado {result.status.value}",
                    results, attempts, started,
                )

            for edge in edges:
                target = edge.target
                # Reintentos en ciclos
                if target in results and results[target].status is not AgentStatus.OK:
                    attempts[target] = attempts.get(target, 0) + 1
                    if attempts[target] > graph.nodes[target].max_retries:
                        self._emit(
                            sink,
                            task,
                            EventType.RETRY,
                            node_id=target,
                            payload={"agotado": True, "attempts": attempts[target]},
                        )
                        return self._abort(
                            task, sink, target,
                            f"reintentos agotados en nodo '{target}'",
                            results, attempts, started,
                        )
                    self._emit(
                        sink,
                        task,
                        EventType.RETRY,
                        node_id=target,
                        payload={"attempt": attempts[target], "max": graph.nodes[target].max_retries},
                    )

                target_entrada = self._build_target_entry(graph, node, edge, result, entrada, bundle)
                frontier.append((target, target_entrada))

        # 6. Fin normal
        duration = int((time.monotonic() - started) * 1000)
        # El resultado final es del ÚLTIMO nodo ejecutado (orden de aparición en results)
        if results:
            last_node_id = list(results.keys())[-1]
            last_result = results[last_node_id]
        else:
            last_result = None

        all_findings: list[Finding] = []
        for r in results.values():
            all_findings.extend(r.findings)

        summary = last_result.summary if last_result and last_result.summary else "Tarea completada"
        output = last_result.output if last_result else None

        result = TaskResult(
            task_id=task.task_id,
            status=TaskStatus.OK,
            summary=summary,
            output=output,
            findings=tuple(all_findings),
            metrics=ExecutionMetrics(
                duration_ms=duration,
                retries=sum(attempts.values()),
            ),
        )
        self._emit(sink, task, EventType.TASK_FINISHED, payload={"status": result.status.value})
        return result

    # --- helpers ---

    def _build_target_entry(self, graph, node, edge, result, entrada, bundle):
        """Construye la entrada del nodo destino según payload y mapping."""
        destino: dict = {
            "task": {},
            "feedback": None,
            "context_refs": list(graph.nodes[edge.target].context_refs),
        }
        if edge.payload == "result":
            destino["task"].update(result.output or {})
            destino["task"].setdefault("goal", entrada.get("task", {}).get("goal", ""))
            destino["task"].setdefault("summary", result.summary)
            if "summary" in (edge.mapping or {}):
                destino["task"][edge.mapping["summary"]] = result.summary
        elif edge.payload == "feedback":
            destino["feedback"] = result.findings
            destino["task"].update(result.output or {})
            destino["task"].setdefault("goal", entrada.get("task", {}).get("goal", ""))
        elif edge.payload == "context":
            destino["context_refs"] = list(bundle.sections) if bundle else []
            destino["task"].update(result.output or {})
            destino["task"].setdefault("goal", entrada.get("task", {}).get("goal", ""))
        elif edge.payload == "none":
            destino["task"] = dict(entrada.get("task", {}))
        # mapping: {campo_target: campo_origen}
        for target_field, source_path in (edge.mapping or {}).items():
            value = _get_path(result, source_path)
            if value is not None:
                destino["task"][target_field] = value
        return destino

    def _abort(self, task, sink, node_id, error, results, attempts, started):
        """Aborta con TaskResult FAILED y emite NODE_STUCK."""
        self._emit(
            sink,
            task,
            EventType.NODE_STUCK,
            node_id=node_id,
            payload={"error": error},
        )
        all_findings = []
        for r in results.values():
            all_findings.extend(r.findings)
        result = TaskResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            summary=f"Tarea falló: {error}",
            error=error,
            findings=tuple(all_findings),
            metrics=ExecutionMetrics(
                duration_ms=int((time.monotonic() - started) * 1000),
                retries=sum(attempts.values()),
            ),
        )
        self._emit(sink, task, EventType.TASK_FINISHED, payload={"status": result.status.value})
        return result

    @staticmethod
    def _emit(sink, task, event_type, node_id=None, payload=None):
        if sink is None:
            return
        sink.emit(
            ExecutionEvent(
                event_type=event_type,
                task_id=task.task_id,
                node_id=node_id,
                payload=payload or {},
            )
        )


def _get_path(obj: Any, path: str | None) -> Any:
    """Extrae un valor de un dict/objeto por ruta de puntos ('output.path')."""
    if not path:
        return None
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current
