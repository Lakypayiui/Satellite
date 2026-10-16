"""LLMAgent: runtime de UN nodo con tools reales y síntesis estructurada."""

from __future__ import annotations

import json
import time
from pathlib import Path

from satellite.agents.base import Agent, AgentContext
from satellite.agents.prompts import render_system, render_template
from satellite.core.context_models import ContextBundle
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.messages import CallContext, LLMRequest, Message, Role, Usage
from satellite.core.result import AgentResult, AgentStatus, ExecutionMetrics, ToolRecord
from satellite.core.spec import AgentSpec
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider
from satellite.providers.prefix import canonical_fingerprint
from satellite.providers.parsing import extract_json_object
from satellite.retrieval.sanitize import UNTRUSTED_RULE, sanitize, wrap_data
from satellite.security.base import SecurityPolicy
from satellite.tools.base import Tool, ToolContext, ToolRegistry


def build_system_prompt(
    system_prompt: str,
    *,
    output_schema: dict | None = None,
    tools: list[Tool] | None = None,
    tools_text: str | None = None,
    can_call=None,
) -> str:
    """System **estático**: resuelve ``{tools}`` y ``{output_schema}`` (build time).

    No admite placeholders dinámicos (``{task}``/``{context}``/``{feedback}``):
    ésos viven en el user template. Esto es lo que hace cacheable el prefijo.
    """
    if tools_text is None:
        # ordenado por id: el system forma parte del prefijo cacheable
        tools_text = (
            "\n".join(
                f"- {tool.id}: {tool.description}" for tool in sorted(tools or [], key=lambda t: t.id)
            )
            or "(sin tools)"
        )
    schema_text = (
        json.dumps(output_schema, ensure_ascii=False, sort_keys=True)
        if output_schema
        else "(sin schema requerido)"
    )
    rendered = render_system(
        system_prompt, tools=tools_text, output_schema=schema_text
    )
    if can_call:
        lines = "\n".join(
            f"- {entry.agent_id}: {entry.description}" for entry in can_call
        )
        rendered += (
            "\n\n# DELEGACIÓN\nPuedes delegar en estos agentes con la tool "
            "`delegate(agent_id, task)`:\n" + lines
        )
    if "no confiable" not in rendered.lower():
        rendered = f"# DATOS NO CONFIABLES\n{UNTRUSTED_RULE}\n\n{rendered}"
    if "fuera del proyecto" not in rendered.lower():
        # Regla de framework para TODOS los agentes ejecutores (también los que crea
        # el usuario): sin ella, al negarse una ruta el modelo improvisaba y acababa
        # presentando el proyecto como si fuera la carpeta que se le pidió.
        regla = (
            "Solo puedes acceder a rutas dentro del proyecto. Si una tool te dice que "
            "una ruta queda fuera, informa del error y de la ruta pedida tal cual "
            "(puede autorizarla el usuario): no sustituyas otra carpeta por ella ni "
            "presentes el proyecto como si fuera lo que se pidió."
        )
        rendered = f"# ALCANCE DE RUTAS\n{regla}\n\n{rendered}"
    return rendered


def build_user_prompt(
    user_template: str,
    *,
    task: dict,
    context: ContextBundle,
    feedback=None,
) -> str:
    """Parte **dinámica**: ``{task}``, ``{context}`` y ``{feedback}``."""
    context_text = _context_to_text(context)
    feedback_text = "\n".join(
        f"- [{f.severity.value}] {sanitize(f.message)}"
        + (f" ({sanitize(f.location)})" if f.location else "")
        for f in (feedback or [])
    ) or "(sin feedback)"
    task_json = json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True)
    return render_template(
        user_template,
        {"task": task_json, "context": context_text, "feedback": feedback_text},
    )


# Qué implica cada tool, para que el modal lo diga en una palabra.
_ACCESS_KIND = {
    "read_file": "lectura",
    "list_dir": "lectura",
    "search_files": "lectura",
    "write_file": "escritura",
    "edit_file": "escritura",
    "run_command": "ejecución",
    "run_argv": "ejecución",
    "run_python": "ejecución",
}


class LLMAgent(Agent):
    """Ejecuta un nodo: LLM → tool_calls → tools → síntesis → AgentResult."""

    def __init__(
        self,
        spec: AgentSpec,
        system_prompt: str,
        user_template: str,
        tools: list[Tool],
        provider: LLMProvider,
        tool_registry: ToolRegistry | None = None,
        policy: SecurityPolicy | None = None,
        context_engine=None,
        project_root: Path | None = None,
        delegation_manager=None,
        delegation_ctx=None,
    ) -> None:
        self.spec = spec
        self.system_prompt = system_prompt
        self.user_template = user_template
        self._spec_fp = canonical_fingerprint(spec.to_dict())
        self.tools = tools
        self.provider = provider
        self.tool_registry = tool_registry
        self.policy = policy
        self.context_engine = context_engine
        self.project_root = project_root
        self.delegation_manager = delegation_manager
        self.delegation_ctx = delegation_ctx
        if self.delegation_ctx is None and delegation_manager is not None:
            root_factory = getattr(delegation_manager, "root_context", None)
            if callable(root_factory):
                self.delegation_ctx = root_factory()

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        """Ejecuta el flujo completo de un nodo."""
        node_id = str(task.get("node_id", ""))
        self._emit(ctx, EventType.NODE_STARTED, {"agent_id": self.spec.id, "task": task})
        started = time.monotonic()
        tool_records: list[ToolRecord] = []
        usage_total = Usage()

        # Si ya venía cancelado, no se gasta ni una llamada al proveedor.
        if getattr(ctx, "cancel", None) is not None and getattr(ctx.cancel, "cancelled", False):
            return self._cancelled(ctx, node_id, started)
        system = build_system_prompt(
            self.system_prompt,
            output_schema=self.spec.output_schema,
            tools=self.tools,
            can_call=self.spec.can_call,
        )
        user = build_user_prompt(
            self.user_template, task=task, context=ctx.context, feedback=ctx.feedback
        )
        messages: list[Message] = [
            Message(role=Role.SYSTEM, content=system),
            Message(role=Role.USER, content=user),
        ]
        call = CallContext(
            task_id=ctx.task_id,
            node_id=ctx.node_id or str(task.get("node_id", "")),
            agent_id=self.spec.id,
            role=self.spec.model_role,
            spec_fp=self._spec_fp,
        )
        final_output: dict | None = None
        error: str | None = None

        # Fase 1: rondas LLM↔tool (máx spec.max_turns)
        remaining = self.spec.max_turns
        while True:
            # Si ya pidieron cancelar, ni se llama: el aborto en vuelo es la red, no la norma.
            if getattr(ctx, "cancel", None) is not None and getattr(
                ctx.cancel, "cancelled", False
            ):
                return self._cancelled(ctx, node_id, started, usage_total, tuple(tool_records))
            response = await self._complete(
                ctx,
                LLMRequest(
                    messages=tuple(messages),
                    tools=tuple(t.schema() for t in self.tools),
                    temperature=0.0,
                    call=call,
                ),
            )
            if response is None:  # cancelaron mientras esperábamos al proveedor
                return self._cancelled(ctx, node_id, started, usage_total, tuple(tool_records))
            usage_total = _accumulate(usage_total, response.usage)
            if response.tool_calls:
                messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content=response.content or "",
                        tool_calls=response.tool_calls,
                    )
                )
                for tool_call in response.tool_calls:
                    # Antes de cada herramienta: si pidieron cancelar, no se lanza.
                    if getattr(ctx, "cancel", None) is not None and getattr(
                        ctx.cancel, "cancelled", False
                    ):
                        return self._cancelled(
                            ctx, node_id, started, usage_total, tuple(tool_records)
                        )
                    record, tool_result = await self._run_tool(tool_call, ctx)
                    tool_records.append(record)
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=json.dumps(
                                {
                                    "success": tool_result.success,
                                    "output": tool_result.output,
                                    "error": tool_result.error,
                                },
                                ensure_ascii=False,
                            ),
                            tool_call_id=tool_call.id,
                        )
                    )
                if remaining > 0:
                    remaining -= 1
                    continue
                else:
                    # sin turnos restantes: sigue a síntesis con lo acumulado
                    response = await self._complete(
                        ctx,
                        LLMRequest(
                            messages=tuple(messages),
                            tools=(),
                            temperature=0.0,
                            call=call,
                        ),
                    )
                    if response is None:
                        return self._cancelled(
                            ctx, node_id, started, usage_total, tuple(tool_records)
                        )
                    usage_total = _accumulate(usage_total, response.usage)
            # Sin tool_calls → intentar síntesis
            break

        if self.spec.output_schema:
            candidate = extract_json_object(response.content or "")
            ok, parsed = self._validate_schema(candidate)
            if not ok:
                messages.append(Message(role=Role.ASSISTANT, content=response.content or ""))
                messages.append(
                    Message(role=Role.USER, content=f"Respuesta no cumple el schema: {parsed}. Devuelve JSON válido.")
                )
                retry = await self._complete(
                    ctx,
                    LLMRequest(messages=tuple(messages), tools=(), temperature=0.0, call=call),
                )
                if retry is None:
                    return self._cancelled(ctx, node_id, started, usage_total, tuple(tool_records))
                usage_total = _accumulate(usage_total, retry.usage)
                candidate = extract_json_object(retry.content or "")
                ok, parsed = self._validate_schema(candidate)
                if not ok:
                    error = f"respuesta no cumple output_schema: {parsed}"
                    final_output = {"content": retry.content or ""}
                else:
                    final_output = parsed
            else:
                final_output = parsed
        else:
            final_output = {"content": response.content or ""}

        duration = int((time.monotonic() - started) * 1000)
        status = AgentStatus.FAILED if error else AgentStatus.OK
        result = AgentResult(
            node_id=node_id,
            spec_id=self.spec.id,
            status=status,
            summary=_summarize(task, final_output, error),
            output=final_output or {},
            tool_calls=tuple(tool_records),
            metrics=ExecutionMetrics(
                tokens_in=usage_total.prompt_tokens,
                tokens_out=usage_total.completion_tokens,
                duration_ms=duration,
            ),
            error=error,
        )
        self._emit(ctx, EventType.NODE_FINISHED, {"status": status.value, "summary": result.summary})
        return result

    async def _complete(self, ctx: AgentContext, request: LLMRequest):
        """Pide respuesta al proveedor; ``None`` si cancelaron mientras esperábamos.

        La llamada en vuelo se aborta al instante en vez de esperarla entera, que es lo
        que hace que cancelar sirva de algo con un proveedor lento.
        """
        from satellite.orchestration.cancel import race_cancel

        valor, cancelado = await race_cancel(getattr(ctx, "cancel", None), self.provider.complete(request))
        return None if cancelado else valor

    def _cancelled(
        self,
        ctx: AgentContext,
        node_id: str,
        started: float,
        usage: Usage | None = None,
        tools: tuple = (),
    ) -> AgentResult:
        """Cierra el nodo como cancelado: ni éxito ni fallo, y diciendo dónde se paró."""
        reason = str(getattr(getattr(ctx, "cancel", None), "reason", "") or "cancelado")
        result = AgentResult(
            node_id=node_id,
            spec_id=self.spec.id,
            status=AgentStatus.CANCELLED,
            summary=f"Nodo cancelado: {reason}",
            output={"cancelled": True, "reason": reason},
            tool_calls=tools,
            metrics=ExecutionMetrics(
                tokens_in=(usage.prompt_tokens if usage else 0),
                tokens_out=(usage.completion_tokens if usage else 0),
                duration_ms=int((time.monotonic() - started) * 1000),
            ),
        )
        self._emit(
            ctx,
            EventType.NODE_FINISHED,
            {"status": result.status.value, "summary": result.summary},
        )
        return result

    async def _ask_for_path(self, call: ToolCall, tool_ctx: ToolContext, result):
        """Pide permiso para una ruta de fuera del proyecto y reintenta si lo dan.

        Convierte la negativa en una decisión del usuario: antes era un callejón sin
        salida y el modelo improvisaba (acabó presentando el proyecto como si fuera la
        carpeta pedida). Las zonas protegidas no llegan aquí: traen otro código y no se
        preguntan nunca.
        """
        from satellite.approval.base import ApprovalContext
        from satellite.core.events import EventType, ExecutionEvent
        from satellite.core.proposal import ApprovalStatus

        details = dict(result.output) if isinstance(result.output, dict) else {}
        resolved = str(details.get("path") or "")
        if not resolved:
            return result
        summary = f"{call.name} pide acceso a {resolved}"
        decision = await tool_ctx.approval.request(
            {"kind": "path", "summary": summary},
            ApprovalContext(
                kind="path",
                task_id=tool_ctx.task_id,
                summary=summary,
                details={
                    "tool": call.name,
                    "path": resolved,
                    "project_root": str(details.get("project_root") or tool_ctx.project_root),
                    "requested": str(details.get("raw") or ""),
                    "consequence": _ACCESS_KIND.get(call.name, "acceso"),
                },
            ),
        )
        if decision is None or getattr(decision, "status", None) is not ApprovalStatus.APPROVED:
            if tool_ctx.sink is not None:
                tool_ctx.sink.emit(
                    ExecutionEvent(
                        event_type=EventType.POLICY_VIOLATION,
                        task_id=tool_ctx.task_id,
                        node_id=tool_ctx.node_id or None,
                        payload={"reason": f"acceso denegado a {resolved}", "tool": call.name},
                    )
                )
            return result
        if str(getattr(decision, "feedback", "") or "") == "session":
            self._grant_for_session(resolved)
        tool_ctx.allowed_paths = tuple(getattr(tool_ctx, "allowed_paths", None) or ()) + (
            Path(resolved),
        )
        return await self.tool_registry.execute(call, tool_ctx)

    def _grant_for_session(self, resolved: str) -> None:
        """Permiso de sesión: la raíz queda concedida en la política."""
        if self.policy is None:
            return
        current = tuple(getattr(self.policy, "allowed_paths", ()) or ())
        self.policy.allowed_paths = current + (Path(resolved),)

    async def _ask_for_command(self, call: ToolCall, tool_ctx: ToolContext, result):
        """Pide permiso para ejecutar un comando y reintenta si lo dan.

        Mismo patrón que las rutas: la negativa deja de ser un callejón sin salida y
        pasa a ser una decisión del usuario (una vez / la sesión / rechazar).
        """
        from satellite.approval.base import ApprovalContext
        from satellite.core.events import EventType, ExecutionEvent
        from satellite.core.proposal import ApprovalStatus

        datos = dict(result.output) if isinstance(result.output, dict) else {}
        argumentos = call.arguments or {}
        comando = str(
            datos.get("command") or argumentos.get("command") or argumentos.get("code") or ""
        ).strip()
        binary = str(datos.get("binary") or (comando.split(" ")[0] if comando else ""))
        if not binary:
            return result
        summary = f"ejecutar: {comando or binary}"
        decision = await tool_ctx.approval.request(
            {"kind": "command", "summary": summary},
            ApprovalContext(
                kind="command",
                task_id=tool_ctx.task_id,
                summary=summary,
                details={
                    "tool": call.name,
                    "command": comando,
                    "binary": binary,
                    "consequence": _ACCESS_KIND.get(call.name, "ejecución"),
                },
            ),
        )
        if decision is None or getattr(decision, "status", None) is not ApprovalStatus.APPROVED:
            if tool_ctx.sink is not None:
                tool_ctx.sink.emit(
                    ExecutionEvent(
                        event_type=EventType.POLICY_VIOLATION,
                        task_id=tool_ctx.task_id,
                        node_id=tool_ctx.node_id or None,
                        payload={"reason": f"comando denegado: {comando or binary}", "tool": call.name},
                    )
                )
            return result
        if str(getattr(decision, "feedback", "") or "") == "session":
            self._grant_command_for_session(binary)
        tool_ctx.allowed_commands = tuple(getattr(tool_ctx, "allowed_commands", None) or ()) + (
            binary,
        )
        return await self.tool_registry.execute(call, tool_ctx)

    def _grant_command_for_session(self, binary: str) -> None:
        """Permiso de sesión para ese binario (espejo de las rutas)."""
        if self.policy is None or not binary:
            return
        current = tuple(getattr(self.policy, "allowed_commands", ()) or ())
        self.policy.allowed_commands = current + (binary,)

    async def _run_tool(self, call: ToolCall, ctx: AgentContext):
        started = time.monotonic()
        tool_ctx = ToolContext(
            project_root=self.project_root or Path("."),
            policy=self.policy,
            sink=ctx.sink,
            task_id=ctx.task_id,
            node_id=ctx.node_id or "",
            engine=self.context_engine,
            delegation=self.delegation_manager,
            delegation_ctx=self.delegation_ctx,
            active_spec=self.spec,
            approval=getattr(ctx, "approval", None),
            # Y el token, para que una tool que lanza un proceso pueda terminarlo.
            cancel=getattr(ctx, "cancel", None),
        )
        result = await self.tool_registry.execute(call, tool_ctx)
        if result.error_code == "path_outside_project" and tool_ctx.approval is not None:
            result = await self._ask_for_path(call, tool_ctx, result)
        elif result.error_code == "command_not_allowed" and tool_ctx.approval is not None:
            result = await self._ask_for_command(call, tool_ctx, result)
        record = ToolRecord(
            tool_name=call.name,
            arguments=call.arguments,
            success=result.success,
            error=result.error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return record, result

    def _validate_schema(self, candidate: dict | None):
        """Devuelve (ok, output o mensaje de error).

        Se valida contra una copia **normalizada** del esquema (el del autor no se toca):
        si declara `properties`, se prohíben las claves extra, y una salida vacía no
        vale. Sin esto, un esquema sin `required` daba por bueno cualquier objeto
        inventado — p.ej. `{"title": …}` en un agente que debía explicar un proyecto — y
        el usuario veía el payload en vez de una respuesta.
        """
        if candidate is None:
            return False, "no se encontró JSON en la respuesta"
        if candidate == {} or candidate == []:
            return False, "la salida está vacía"
        try:
            import copy

            import jsonschema

            schema = self.spec.output_schema or {}
            if isinstance(schema, dict) and schema.get("properties"):
                schema = copy.deepcopy(schema)
                schema.setdefault("additionalProperties", False)
            jsonschema.validate(candidate, schema)
            return True, candidate
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def _emit(self, ctx: AgentContext, event_type: EventType, payload: dict) -> None:
        if ctx.sink is None:
            return
        ctx.sink.emit(
            ExecutionEvent(
                event_type=event_type,
                task_id=ctx.task_id,
                node_id=ctx.node_id or None,
                payload=payload,
            )
        )


def _accumulate(total: Usage, part: Usage | None) -> Usage:
    if part is None:
        return total
    return Usage(
        prompt_tokens=total.prompt_tokens + part.prompt_tokens,
        completion_tokens=total.completion_tokens + part.completion_tokens,
        total_tokens=total.total_tokens + part.total_tokens,
    )


def _context_to_text(context: ContextBundle) -> str:
    if not context.sections:
        return "(sin contexto)"
    return "\n\n".join(
        wrap_data(f"{section.ref.kind.value}:{section.ref.value}", section.content)
        for section in context.sections
    )


SUMMARY_LIMIT = 200


def _clip_summary(text: object) -> str:
    """Un resumen es una línea: sin saltos y con tope, para el log de eventos."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= SUMMARY_LIMIT else flat[: SUMMARY_LIMIT - 1] + "…"


def _summarize(task: dict, output: dict | None, error: str | None) -> str:
    if error:
        return f"Node falló: {error}"
    if output and output.get("path"):
        return f"Trabajó en {output['path']}"
    if output and output.get("summary"):
        return str(output["summary"])
    if output and output.get("content"):
        # El texto que respondió el LLM. Sin esto el resumen era "Node completado" y
        # la respuesta se quedaba en el output sin que nadie la pintara.
        return _clip_summary(output["content"])
    if output:
        # Un nodo con `output_schema` devuelve sus propias claves: se resume con el
        # JSON en una línea antes que perderlo y decir "Node completado".
        try:
            import json

            rendered = json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover
            rendered = ""
        if rendered and rendered not in ("{}", "null"):
            return _clip_summary(rendered)
    return "Node completado"
