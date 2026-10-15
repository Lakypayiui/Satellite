"""DefaultOrchestrator: plan → runner → supervisión → resultado.

Consume la salida del agente `orchestrator` (plan | capability_gap |
needs_clarification). El chat-directo pasa a ser una salida `direct_reply`
(configurable) y el preprocesado (refine_goal) queda como etapa opcional.
"""

from __future__ import annotations

from satellite.core.errors import ContractError
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.messages import LLMRequest, Message, Role
from satellite.core.result import ExecutionMetrics
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.events.base import EventSink
from satellite.orchestration.base import (
    GraphRunner,
    Orchestrator,
    Planner,
    PlanningResult,
    RuntimeServices,
)
from satellite.orchestration.catalog_answer import answer_catalog_question
from satellite.orchestration.chat_detector import async_is_chat_goal
from satellite.persistence.base import Store


class DefaultOrchestrator(Orchestrator):
    """Coordina planificación, ejecución y replanificación acotada."""

    def __init__(
        self,
        planner: Planner,
        runner: GraphRunner,
        services: RuntimeServices,
        store: Store | None = None,
        sink: EventSink | None = None,
        allow_chat_direct: bool = True,
        expander: object | None = None,
        plan_advisor: object | None = None,
    ) -> None:
        self.planner = planner
        self.runner = runner
        self.services = services
        self.store = store
        self.sink = sink
        self.allow_chat_direct = allow_chat_direct
        self.preprocessor = None  # refinado opcional del goal (opt-in)
        self.expander = expander  # AgentExpander (opt-in)
        self.plan_advisor = plan_advisor  # PlanOptionGenerator (opt-in)
        self.max_replans = 1

    async def execute(self, task: Task) -> TaskResult:
        """Ejecuta una tarea de principio a fin."""
        self._emit(task, EventType.TASK_STARTED, {"goal": task.goal})
        if self.store is not None:
            await self.store.save_task(task)

        if self.preprocessor is not None:
            task, early = await self._preprocess(task)
            if early is not None:
                return await self._finish(task, early)

        # Elegir una alternativa del plan es una orden de ejecución: no puede acabar
        # en el atajo del catálogo («tengo N skills guardadas») ni en una respuesta
        # directa de charla, que era justo lo que pasaba al elegir una opción.
        execute_choice = bool(task.metadata.get("execute_choice"))

        if self.allow_chat_direct and not execute_choice:
            # Preguntas sobre el propio sistema ("¿qué agentes tienes?", "¿qué
            # herramientas?", "¿qué modelo eres?"): se contestan localmente desde
            # el catálogo real, sin LLM y sin grafo.
            local = answer_catalog_question(task.goal, self.services)
            if local is not None:
                return await self._finish(
                    task,
                    TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.OK,
                        summary=local,
                        output={"mode": "direct_reply", "content": local},
                    ),
                )

        if self.allow_chat_direct and not execute_choice and self.services.provider is not None:
            if await async_is_chat_goal(task, self.services.provider):
                return await self._finish(task, await self._answer_direct(task))

        # Modo plan explícito: el usuario pidió planificar → alternativas, sin
        # exigir que la planificación normal tenga éxito antes.
        allow_options = bool(task.metadata.get("allow_plan_options", True))
        forced_plan = bool(task.metadata.get("plan_mode"))
        if self.plan_advisor is not None and allow_options and forced_plan:
            surfaced = await self._plan_options(task, None, required=True)
            if surfaced is not None:
                return await self._finish(task, surfaced)

        planning = await self.planner.plan(task, self.services.registry)

        # Tarea ambigua (el orquestador devolvió needs_clarification) → alternativas.
        if (
            self.plan_advisor is not None
            and allow_options
            and planning.kind == "needs_clarification"
        ):
            surfaced = await self._plan_options(task, planning)
            if surfaced is not None:
                return await self._finish(task, surfaced)

        if planning.kind == "capability_gap":
            expanded = await self._try_expand(task, planning)
            if expanded is not None:
                planning = expanded
        if planning.kind != "plan":
            return await self._finish(task, self._non_plan_result(task, planning))

        graph = planning.graph
        if graph is None:
            raise ContractError("planner devolvió kind='plan' sin grafo")
        if self.store is not None:
            await self.store.save_graph(task.task_id, graph)
        # GRAPH_BUILT del plan inicial lo emite el runner al arrancar el grafo;
        # aquí solo se emite el de un replan (que el runner no puede saber).

        result = await self.runner.run(graph, task, self.services)

        replans = 0
        while result.status is TaskStatus.FAILED and replans < self.max_replans:
            replans += 1
            replanning = await self._replan(task)
            if replanning is None:
                break
            if replanning.kind != "plan" or replanning.graph is None:
                result = self._non_plan_result(task, replanning)
                break
            if self.store is not None:
                await self.store.save_graph(task.task_id, replanning.graph)
            self._emit(
                task,
                EventType.GRAPH_BUILT,
                {"nodes": len(replanning.graph.nodes), "replan": True},
            )
            result = await self.runner.run(replanning.graph, task, self.services)

        return await self._finish(task, result)

    # --- helpers ---

    async def _preprocess(self, task: Task) -> tuple[Task, TaskResult | None]:
        """Etapa opcional de refinado del goal (desactivada por defecto)."""
        try:
            prep = await self.preprocessor.preprocess(task.goal)
        except Exception:
            return task, None
        if prep.get("needs_user_input"):
            user_prompt = prep.get("user_prompt") or "Necesito más información para continuar."
            return task, TaskResult(
                task_id=task.task_id,
                status=TaskStatus.NEEDS_USER_INPUT,
                summary=user_prompt,
                output={
                    "mode": "needs_user_input",
                    "user_prompt": user_prompt,
                },
            )
        refined = prep.get("refined_prompt")
        if refined and refined != task.goal:
            task = Task(
                goal=refined,
                project_root=task.project_root,
                metadata={**task.metadata, "refined_from": task.goal},
                task_id=task.task_id,
            )
        return task, None

    def _non_plan_result(self, task: Task, planning: PlanningResult) -> TaskResult:
        """Convierte un gap o una aclaración en TaskResult (sin ejecutar grafo)."""
        if planning.kind == "needs_clarification":
            question = planning.question or "Necesito más información para continuar."
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.NEEDS_USER_INPUT,
                summary=question,
                output={"mode": "needs_clarification", "question": question},
            )
        gap = planning.gap
        missing = gap.missing if gap is not None else "capacidad desconocida"
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.NEEDS_USER_INPUT,
            summary=f"Falta capacidad: {missing}",
            output={
                "mode": "capability_gap",
                "capability_gap": gap.to_dict() if gap is not None else None,
            },
        )

    async def _answer_direct(self, task: Task) -> TaskResult:
        """Responde una consulta conversacional con el LLM (sin tools ni grafo).

        Incluye la conversación reciente: sin ella, un seguimiento como «explícamela»
        llegaba sin antecedente y el modelo contestaba una obviedad.
        """
        from satellite.orchestration.planner_prompt import render_history

        conversacion = render_history(task.metadata.get("history")).strip()
        trozos = [
            "El usuario hace una consulta de tipo conversacional. Responde de "
            "forma clara y concisa, en el idioma del usuario. NO menciones tools, "
            "agentes ni grafos.",
            "Si el mensaje se refiere a algo ya dicho («eso», «explícamelo», «y eso "
            "por qué»), resuélvelo con la conversación de abajo. Si de verdad falta "
            "el antecedente, dilo en vez de inventarlo.",
        ]
        if conversacion:
            trozos.append(f"CONVERSACIÓN RECIENTE:\n{conversacion}")
        trozos.append(f"Consulta del usuario:\n{task.goal}")
        prompt = "\n\n".join(trozos)
        request = LLMRequest(
            messages=(
                Message(
                    role=Role.SYSTEM,
                    content="Eres la consola de Satellite en modo conversación simple.",
                ),
                Message(role=Role.USER, content=prompt),
            ),
            temperature=0.0,
        )
        response = await self.services.provider.complete(request)
        answer = (response.content or "").strip() or "(sin respuesta)"
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.OK,
            summary=answer,
            output={"mode": "direct_reply", "content": answer},
            metrics=ExecutionMetrics(
                tokens_in=response.usage.prompt_tokens if response.usage else 0,
                tokens_out=response.usage.completion_tokens if response.usage else 0,
            ),
        )

    async def _replan(self, task: Task) -> PlanningResult | None:
        """Replanifica una vez; None si el planner falla (pero deja rastro)."""
        try:
            return await self.planner.plan(task, self.services.registry, context_hint=())
        except Exception as exc:  # noqa: BLE001 — se reporta, no se esconde
            # El fallback se mantiene (un planner que falla pregunta en vez de
            # ejecutar), pero deja de ser invisible.
            self._emit(
                task,
                EventType.PLAN_FAILED,
                {"kind": "replan", "reason": f"el planner falló: {exc}"},
            )
            return None

    async def _plan_options(
        self,
        task: Task,
        planning: PlanningResult | None = None,
        *,
        required: bool = False,
    ) -> TaskResult | None:
        """Genera alternativas (modo plan); ``None`` si el asesor no las produce.

        ``required`` distingue el modo plan **pedido explícitamente** (fallar ahí es
        un error que hay que decir) de la mejora automática ante una tarea ambigua
        (ahí se mantiene el fallback: la clarificación del planner sigue valiendo).
        En los dos casos se emite ``PLAN_FAILED``: nunca desaparece en silencio.
        """
        from satellite.orchestration.planner_prompt import (
            build_catalog_context,
            render_history,
        )

        registry = self.services.registry
        specs = registry.all() if registry is not None else []
        # Catálogo = bloque estable; historial = dinámico (no rompe la caché).
        context = build_catalog_context(specs)
        history = render_history(task.metadata.get("history"))
        question = planning.question if planning is not None else ""
        try:
            options = await self.plan_advisor.generate(
                task.goal, context=context, history=history, feedback=question
            )
        except Exception as exc:  # noqa: BLE001 — se reporta, no se esconde
            reason = f"el asesor de plan falló: {exc}"
            self._emit(task, EventType.PLAN_FAILED, {"kind": "error", "reason": reason})
            if not required:
                return None
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"el modo plan no pudo generar alternativas: {exc}",
            )
        if options is None:
            reason = "el asesor no produjo alternativas válidas"
            self._emit(task, EventType.PLAN_FAILED, {"kind": "empty", "reason": reason})
            if not required:
                return None
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.NEEDS_USER_INPUT,
                summary=(
                    "No pude generar alternativas para esa tarea. Reformúlala o "
                    "cuéntame algo más de contexto."
                ),
                error=reason,
            )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.NEEDS_USER_INPUT,
            summary=(
                f"Tengo {len(options.options)} alternativas. Elige una o describe "
                "tu propio enfoque."
            ),
            output={
                "mode": "plan_options",
                "plan_options": options.to_dict(),
                "question": options.question or question,
            },
        )

    async def _try_expand(
        self, task: Task, planning: PlanningResult
    ) -> PlanningResult | None:
        """Intenta cubrir un ``capability_gap`` creando un agente nuevo.

        Devuelve un ``PlanningResult`` si la expansión cambió el panorama
        (replanificación), o ``None`` para conservar el resultado de gap.
        """
        if self.expander is None or planning.gap is None:
            return None
        try:
            outcome = await self.expander.expand(
                planning.gap.missing,
                task_id=task.task_id,
                approval_port=self.services.approval,
            )
        except Exception:
            return None

        if outcome.kind in ("created", "extended", "reuse", "skill_created"):
            return await self._replan(task)
        if outcome.kind == "skill_suggested":
            return PlanningResult(
                kind="needs_clarification",
                question=(
                    "Esto parece una transformación determinista, no un rol: "
                    f"conviene una skill ({outcome.reason}). ¿Quieres que la cree?"
                ),
            )
        if outcome.kind == "needs_clarification":
            return PlanningResult(kind="needs_clarification", question=outcome.question)
        return None

    async def _finish(self, task: Task, result: TaskResult) -> TaskResult:
        """Persiste el estado final y emite TASK_FINISHED."""
        if self.store is not None:
            await self.store.save_state(task.task_id, {"status": result.status.value})
        self._emit(task, EventType.TASK_FINISHED, {"status": result.status.value})
        return result

    def _emit(self, task: Task, event_type: EventType, payload: dict) -> None:
        if self.sink is None:
            return
        self.sink.emit(
            ExecutionEvent(event_type=event_type, task_id=task.task_id, payload=payload)
        )
