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
        self._emit(ctx, EventType.NODE_STARTED, {"spec": self.spec.id, "task": task})
        started = time.monotonic()
        tool_records: list[ToolRecord] = []
        usage_total = Usage()
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
            response = await self.provider.complete(
                LLMRequest(
                    messages=tuple(messages),
                    tools=tuple(t.schema() for t in self.tools),
                    temperature=0.0,
                    call=call,
                )
            )
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
                    response = await self.provider.complete(
                        LLMRequest(
                            messages=tuple(messages),
                            tools=(),
                            temperature=0.0,
                            call=call,
                        )
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
                retry = await self.provider.complete(
                    LLMRequest(messages=tuple(messages), tools=(), temperature=0.0, call=call)
                )
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
        )
        result = await self.tool_registry.execute(call, tool_ctx)
        record = ToolRecord(
            tool_name=call.name,
            arguments=call.arguments,
            success=result.success,
            error=result.error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return record, result

    def _validate_schema(self, candidate: dict | None):
        """Devuelve (ok, output o mensaje de error)."""
        if candidate is None:
            return False, "no se encontró JSON en la respuesta"
        try:
            import jsonschema

            jsonschema.validate(candidate, self.spec.output_schema)
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
