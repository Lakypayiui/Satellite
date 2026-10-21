"""LLMAgent: runtime de UN nodo con tools reales y síntesis estructurada."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

from satellite.agents.base import Agent, AgentContext
from satellite.agents.prompts import render_system, render_template
from satellite.agents.subagents import SpawnGuard, SpawnManager
from satellite.core.context_models import ContextBundle
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.language import output_language_hint, with_output_language
from satellite.core.messages import CallContext, LLMRequest, Message, Role, Usage
from satellite.core.result import (
    AgentResult,
    AgentStatus,
    ExecutionMetrics,
    Finding,
    FindingSeverity,
    ToolRecord,
)
from satellite.core.spec import AgentSpec
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider
from satellite.providers.prefix import canonical_fingerprint
from satellite.providers.parsing import extract_json_object
from satellite.retrieval.sanitize import UNTRUSTED_RULE, sanitize, wrap_data
from satellite.security.base import SecurityPolicy
from satellite.tools.base import Tool, ToolContext, ToolRegistry


SPAWN_RULES = """# SUBAGENTS
You can create up to four subagents with `spawn_agent`, but splitting is the exception, not the rule. Decide at the start, before writing code or calling tools:
- Solve the task yourself (no children) when it is short, sequential (each step needs the previous one), touches few files or lines, coordination and contract definition would cost more than the time saved, or you are unsure.
- Split only when it is clearly large and separable into independent parts: reading several files that do not depend on each other, writing several files from scratch, or a very long code task with separable parts.
- When in doubt, do not split.

If you decide to split:
1. Create only the children you really need, between one and four.
2. Define the contracts before spawning: function signatures, filenames, data formats and expected imports.
3. Give each child exactly one file or one clearly delimited region as exclusive ownership. Never assign the same file or function to two children. If two children must touch the same file, assign disjoint regions, instruct them to make targeted edits instead of rewriting the file, and state explicitly which parts belong to the other child.
4. Each child message must include its exact task, the file or region it owns, the files it may only read, the interfaces with the other children's work (which functions will exist, with which signatures, and that it must NOT implement them), and the restrictions on what it must not touch. It must be enough to work without hallucinating or overwriting another child's work, but must not include the full task context.
5. You count as one of the five agents and also work on your part while the children run.
6. When all children finish, integrate and verify that the result works as a whole (imports, signatures, consistency), fix incompatibilities, and report a short summary to the orchestrator.
7. If a child fails or times out, you receive `failed` with the reason. Decide whether to retry, do that part yourself or report the error. The other children are not cancelled.
8. If you decide not to split, do the whole task yourself and report normally without mentioning subagents.
"""


def build_system_prompt(
    system_prompt: str,
    *,
    output_schema: dict | None = None,
    tools: list[Tool] | None = None,
    tools_text: str | None = None,
    can_call=None,
    spawn_enabled: bool = False,
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
            or "(no tools)"
        )
    schema_text = (
        json.dumps(output_schema, ensure_ascii=False, sort_keys=True)
        if output_schema
        else "(no schema required)"
    )
    rendered = render_system(
        system_prompt, tools=tools_text, output_schema=schema_text
    )
    # Un agente generado puede olvidar el placeholder; sin el schema a la vista el
    # modelo inventa claves y el runtime rechaza una respuesta que era válida.
    if output_schema and "{output_schema}" not in system_prompt:
        rendered += f"\n\n# OUTPUT SCHEMA\n{schema_text}"
    if can_call:
        lines = "\n".join(
            f"- {entry.agent_id}: {entry.description}" for entry in can_call
        )
        rendered += (
            "\n\n# DELEGATION\nYou may delegate to these agents with the "
            "`delegate(agent_id, task)` tool:\n" + lines
        )
    if spawn_enabled and "# SUBAGENTS" not in rendered:
        rendered += "\n\n" + SPAWN_RULES
    if "untrusted" not in rendered.lower():
        rendered = f"# UNTRUSTED DATA\n{UNTRUSTED_RULE}\n\n{rendered}"
    if "outside the project" not in rendered.lower():
        # Regla de framework para TODOS los agentes ejecutores (también los que crea
        # el usuario): sin ella, al negarse una ruta el modelo improvisaba y acababa
        # presentando el proyecto como si fuera la carpeta que se le pidió.
        regla = (
            "You may only access paths inside the project. If a tool tells you that a "
            "path falls outside, report the error and the requested path exactly as "
            "given (the user may authorize it): do not substitute another folder for it "
            "and do not present the project as if it were what was requested."
        )
        rendered = f"# PATH SCOPE\n{regla}\n\n{rendered}"
    if tools and "# PROJECT ACCESS" not in rendered:
        # Regla de framework para todos los agentes con tools: los archivos del
        # proyecto se leen con tools, no se piden al usuario. El README es
        # documentación, no la especificación del comportamiento.
        regla = (
            "You have direct access to the project through your tools. Never ask the "
            "user to paste a project file, show its contents, or confirm that it "
            "exists: use list_dir, search_files and read_file. If a path is not where "
            "you expected, search for it.\n"
            "The README is documentation, not the specification. For code tasks, "
            "verify behavior against the code, tests and build files. Do not run git "
            "or git status unless the task is specifically about changes, diffs or "
            "history.\n"
            "If the user says \"do it\", \"hazlo\", \"proceed\" or similar, execute "
            "the concrete next action from the recent conversation with the tools "
            "your role allows instead of asking again."
        )
        rendered += f"\n\n# PROJECT ACCESS\n{regla}"
    # Regla común de idioma: los prompts internos van en inglés, pero el texto que el
    # usuario lee sigue el idioma de su mensaje/conversación.
    return with_output_language(rendered)


def build_user_prompt(
    user_template: str,
    *,
    task: dict,
    context: ContextBundle,
    feedback=None,
    user_language: str = "",
) -> str:
    """Parte **dinámica**: ``{task}``, ``{context}`` y ``{feedback}``.

    The language hint is an internal instruction and stays in English; only the
    language it names/is detected follows the user.
    """
    context_text = _context_to_text(context)
    feedback_text = "\n".join(
        f"- [{f.severity.value}] {sanitize(f.message)}"
        + (f" ({sanitize(f.location)})" if f.location else "")
        for f in (feedback or [])
    ) or "(no feedback)"
    task_json = json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True)
    language_hint = output_language_hint(user_language) + "\n\n"
    rendered = render_template(
        user_template,
        {
            "task": task_json,
            "context": context_text,
            "feedback": feedback_text,
        },
    )
    return language_hint + rendered


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
        workdir: Path | None = None,
        sandbox=None,
        delegation_manager=None,
        delegation_ctx=None,
        skills=None,
        spawn_child_provider=None,
        spawn_manager=None,
        spawn_guard: SpawnGuard | None = None,
        is_child: bool = False,
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
        # Raíz donde las tools escriben/ejecutan (worktree activo). Si es None, el
        # ``ToolContext`` cae a ``project_root``: el comportamiento de siempre.
        self.workdir = workdir
        # Sandbox efectivo del nivel configurado. Viaja al ``ToolContext`` para que
        # ``run_command``/``run_argv``/``run_python`` ejecuten por él (y fallen cerrado
        # si su backend no está disponible) en vez de correr sin confinamiento.
        self.sandbox = sandbox
        self.delegation_manager = delegation_manager
        self.delegation_ctx = delegation_ctx
        self.skills = skills
        # Parent-only spawn manager. Children receive a guard instead and can never
        # create more agents, even if the model tries to call the tool.
        self.spawn_child_provider = spawn_child_provider
        self.spawn_manager = spawn_manager
        self.spawn_guard = spawn_guard
        self.is_child = bool(is_child or spawn_guard is not None)
        self._active_ctx: AgentContext | None = None
        if not self.is_child and self.spawn_manager is None:
            if any(tool.id == "spawn_agent" for tool in self.tools):
                self.spawn_manager = SpawnManager(self)
        # Shared with every ToolContext call of this execution (see AgentContext).
        self._task_state: dict = {}
        if self.delegation_ctx is None and delegation_manager is not None:
            root_factory = getattr(delegation_manager, "root_context", None)
            if callable(root_factory):
                self.delegation_ctx = root_factory()

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        """Ejecuta el flujo completo de un nodo."""
        node_id = str(task.get("node_id", ""))
        self._task_state = ctx.task_state
        self._active_ctx = ctx
        self._emit(ctx, EventType.NODE_STARTED, {"agent_id": self.spec.id, "task": task})
        if self.delegation_ctx is not None and hasattr(self.delegation_ctx, "user_language"):
            # The root delegation context is created by the factory before a task
            # exists; inherit the real task id here or every delegated event crashes.
            updates = {"user_language": ctx.user_language}
            if ctx.task_id:
                updates["task_id"] = ctx.task_id
            self.delegation_ctx = replace(self.delegation_ctx, **updates)
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
            spawn_enabled=self.spawn_manager is not None,
        )
        user = build_user_prompt(
            self.user_template,
            task=task,
            context=ctx.context,
            feedback=ctx.feedback,
            user_language=ctx.user_language,
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
                # All spawn calls of one turn run concurrently. The parent waits for
                # every child before continuing, so it can integrate the complete set.
                runs: dict[str, tuple] = {}
                spawn_calls = [
                    call for call in response.tool_calls if call.name == "spawn_agent"
                ]
                spawn_tasks: dict[str, asyncio.Task] = {}
                # Start the children first, then run the parent's own tools while they
                # are still working. Both sides are independent by construction: they
                # were requested in the same assistant turn.
                if spawn_calls:
                    spawn_tasks = {
                        call.id: asyncio.create_task(self._run_tool(call, ctx))
                        for call in spawn_calls
                    }
                for tool_call in response.tool_calls:
                    if tool_call.id in spawn_tasks:
                        continue
                    # Before each non-spawn tool: stop if cancellation was requested.
                    if getattr(ctx, "cancel", None) is not None and getattr(
                        ctx.cancel, "cancelled", False
                    ):
                        for task in spawn_tasks.values():
                            task.cancel()
                        if spawn_tasks:
                            await asyncio.gather(
                                *spawn_tasks.values(), return_exceptions=True
                            )
                        for done in response.tool_calls:
                            if done.id in runs:
                                tool_records.append(runs[done.id][0])
                        return self._cancelled(
                            ctx, node_id, started, usage_total, tuple(tool_records)
                        )
                    runs[tool_call.id] = await self._run_tool(tool_call, ctx)
                if spawn_tasks:
                    gathered = await asyncio.gather(
                        *spawn_tasks.values(), return_exceptions=True
                    )
                    for call, item in zip(spawn_calls, gathered):
                        if isinstance(item, BaseException):
                            runs[call.id] = self._failed_tool(call, item)
                        else:
                            runs[call.id] = item
                # Preserve the model's tool_call order in the conversation even
                # though spawn calls were executed first/concurrently.
                for tool_call in response.tool_calls:
                    record, tool_result = runs.get(
                        tool_call.id,
                        self._failed_tool(
                            tool_call, RuntimeError("tool call produced no result")
                        ),
                    )
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

        dropped: list[str] = []
        if self.spec.output_schema:
            candidate = extract_json_object(response.content or "")
            ok, parsed, dropped = self._validate_schema(candidate)
            if not ok:
                messages.append(Message(role=Role.ASSISTANT, content=response.content or ""))
                messages.append(
                    Message(
                        role=Role.USER,
                        content=_schema_retry_message(self.spec.output_schema, parsed),
                    )
                )
                retry = await self._complete(
                    ctx,
                    LLMRequest(messages=tuple(messages), tools=(), temperature=0.0, call=call),
                )
                if retry is None:
                    return self._cancelled(ctx, node_id, started, usage_total, tuple(tool_records))
                usage_total = _accumulate(usage_total, retry.usage)
                candidate = extract_json_object(retry.content or "")
                ok, parsed, dropped = self._validate_schema(candidate)
                if not ok:
                    # El reintento tampoco cumple. Si el esquema no exige ninguna clave
                    # (todo opcional) y la respuesta sí traía datos, se conservan tal cual
                    # en vez de tirarlos: un `output_schema` sin `required` queda satisfecho
                    # aunque el modelo use otros nombres (p.ej. su prompt pide secciones en
                    # español y el schema las declara en inglés), así que no puede acabar en
                    # una "salida vacía" que tumbe el nodo entero.
                    salvado = _salvage_undeclared_only(self.spec.output_schema, candidate)
                    if salvado is not None:
                        final_output = salvado
                        dropped = _prune_to_schema(self.spec.output_schema, salvado)[1]
                    else:
                        error = f"response does not match output_schema: {parsed}"
                        final_output = {"content": retry.content or ""}
                        dropped = []
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
            findings=_schema_findings(dropped),
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

    def _failed_tool(self, call: ToolCall, exc: BaseException):
        from satellite.core.tool_models import ToolResult

        message = f"tool call failed: {exc}"
        result = ToolResult(
            call_id=call.id,
            success=False,
            error=message,
            error_code="tool_runtime_error",
        )
        record = ToolRecord(
            tool_name=call.name,
            arguments=dict(call.arguments or {}),
            success=False,
            error=message,
            duration_ms=0,
        )
        return record, result

    def _child_tool_denial(self, call: ToolCall) -> tuple[str, str] | None:
        """Code-enforced boundary for subagents.

        The child prompt states the rules; this method makes them real even if the
        model ignores them. It returns ``(message, error_code)`` or ``None``.
        """
        guard = self.spawn_guard
        if guard is None:
            return None
        if call.name in {"spawn_agent", "delegate"}:
            return ("Subagents cannot create other agents.", "subagent_cannot_spawn")
        if call.name == "request_context":
            return (
                "Subagents do not receive the parent conversation.",
                "subagent_no_parent_context",
            )
        if call.name in {"run_command", "run_argv", "run_python", "job"}:
            return (
                "Subagents cannot run commands. The parent runs and verifies them "
                "after integration.",
                "subagent_command_denied",
            )
        if call.name == "propose_skill":
            return (
                "Subagents cannot create skills.",
                "subagent_skill_denied",
            )
        if call.name.startswith("desktop_"):
            return (
                "Subagents cannot control the desktop.",
                "subagent_desktop_denied",
            )
        if call.name in {"write_file", "edit_file", "render_diagram"}:
            arguments = dict(call.arguments or {})
            path = _path_from_arguments(arguments)
            if not guard.allows_tool(call.name, arguments):
                detail = (
                    "Region owners must use edit_file for focused changes; "
                    "write_file/render_diagram would replace the whole file. "
                    if guard.can_write(path)
                    else ""
                )
                return (
                    "Subagent may only modify its owned files or regions. "
                    + detail
                    + f"Owned: {guard.owned_labels}. Attempted: {path or '(no path)'}.",
                    "subagent_path_not_owned",
                )
        return None

    async def _run_tool(self, call: ToolCall, ctx: AgentContext):
        started = time.monotonic()
        denial = self._child_tool_denial(call)
        if denial is not None:
            from satellite.core.tool_models import ToolResult

            message, code = denial
            result = ToolResult(
                call_id=call.id,
                success=False,
                error=message,
                error_code=code,
            )
            if ctx.sink is not None:
                ctx.sink.emit(
                    ExecutionEvent(
                        event_type=EventType.TOOL_CALLED,
                        task_id=ctx.task_id,
                        node_id=ctx.node_id or None,
                        tool_name=call.name,
                        payload={
                            "arguments": call.arguments,
                            "success": False,
                            "error": message,
                            "output": None,
                        },
                    )
                )
            record = ToolRecord(
                tool_name=call.name,
                arguments=call.arguments,
                success=False,
                error=message,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return record, result
        tool_ctx = ToolContext(
            project_root=self.project_root or Path("."),
            workdir=self.workdir,
            policy=self.policy,
            sink=ctx.sink,
            task_id=ctx.task_id,
            session_id=getattr(ctx, "session_id", ""),
            node_id=ctx.node_id or "",
            engine=self.context_engine,
            delegation=self.delegation_manager,
            delegation_ctx=self.delegation_ctx,
            active_spec=self.spec,
            skills=self.skills,
            task_state=self._task_state,
            approval=getattr(ctx, "approval", None),
            # Y el token, para que una tool que lanza un proceso pueda terminarlo.
            cancel=getattr(ctx, "cancel", None),
            # El sandbox del nivel configurado: los procesos del agente corren por él.
            sandbox=self.sandbox,
            spawn_manager=self.spawn_manager,
            is_child=self.is_child,
        )
        result = await self.tool_registry.execute(call, tool_ctx)
        if result.error_code == "path_outside_project" and tool_ctx.approval is not None:
            result = await self._ask_for_path(call, tool_ctx, result)
        elif result.error_code == "command_not_allowed" and tool_ctx.approval is not None:
            result = await self._ask_for_command(call, tool_ctx, result)
        if result.success and call.name in ("run_command", "run_argv", "run_python"):
            # Evidence for propose_skill: a successful execution happened in this task.
            self._task_state.setdefault("successful_runs", []).append(
                {"tool": call.name, "arguments": dict(call.arguments)}
            )
        record = ToolRecord(
            tool_name=call.name,
            arguments=call.arguments,
            success=result.success,
            error=result.error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return record, result

    def _validate_schema(self, candidate: dict | None):
        """Devuelve (ok, output normalizado o error, claves descartadas).

        Se valida contra el esquema del autor, pero **recortando antes** las claves que
        no declara: los nodos suelen devolver campos de más (``title``, ``notes``…)
        junto a los correctos, y prohibirlos tumbaba la respuesta entera (``Additional
        properties are not allowed``) perdiendo también los datos válidos. Recortar
        conserva lo declarado — con sus tipos y ``required`` — y sólo cuenta como
        vacía la respuesta que *únicamente* traía claves inventadas.
        """
        if candidate is None:
            return False, "no JSON found in the response", []
        if candidate == {} or candidate == []:
            return False, "the output is empty", []
        try:
            import jsonschema

            schema = self.spec.output_schema or {}
            recortado, descartadas = _prune_to_schema(schema, candidate)
            if recortado == {} or recortado == []:
                detalle = (
                    " (it only carried undeclared keys: "
                    + ", ".join(sorted(dict.fromkeys(descartadas)))
                    + ")"
                    if descartadas
                    else ""
                )
                return False, f"the output is empty{detalle}", descartadas
            jsonschema.validate(recortado, schema)
            return True, recortado, descartadas
        except Exception as exc:  # noqa: BLE001
            return False, str(exc), []

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


def _prune_to_schema(schema, value, _path: str = ""):
    """Recorta de ``value`` las claves que ``schema`` no declara.

    Devuelve ``(valor_normalizado, claves_descartadas)``. Sólo recorta donde el
    esquema declara ``properties`` y no admite adicionales (``additionalProperties``
    ausente o ``False``); si el esquema los admite (``true`` o un subesquema), respeta
    la salida tal cual. Recorre anidados (objetos y arrays) para que una clave extra
    profunda no tumbe la validación.
    """
    if not isinstance(schema, dict):
        return value, []
    if isinstance(value, dict):
        props = schema.get("properties")
        if not isinstance(props, dict) or not props:
            return value, []
        if schema.get("additionalProperties", False) is not False:
            return value, []
        normalizado: dict = {}
        descartadas: list[str] = []
        for clave, sub in value.items():
            if clave in props:
                hijo, extra = _prune_to_schema(props[clave], sub, f"{_path}{clave}.")
                normalizado[clave] = hijo
                descartadas.extend(extra)
            else:
                descartadas.append(f"{_path}{clave}")
        return normalizado, descartadas
    items = schema.get("items")
    if isinstance(value, list) and isinstance(items, dict):
        normalizado = []
        descartadas = []
        for elemento in value:
            hijo, extra = _prune_to_schema(items, elemento, f"{_path}[].")
            normalizado.append(hijo)
            descartadas.extend(extra)
        return normalizado, descartadas
    return value, []


def _declared_keys(schema: object) -> list[str]:
    """Claves de primer nivel que declara el ``output_schema`` (vacío si no declara)."""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    return [str(clave) for clave in props]


def _schema_retry_message(schema: dict, problem: str) -> str:
    """Pide de nuevo JSON válido y, si el esquema declara claves, las nombra.

    Sin la lista de claves, el reintento era a ciegas: el modelo no sabía qué nombres
    exactos debía usar y volvía a fallar igual (el caso de un prompt que pide secciones
    con otros nombres que el schema).
    """
    pista = ""
    declaradas = _declared_keys(schema)
    if declaradas:
        pista = " Use exactly these top-level keys: " + ", ".join(declaradas) + "."
    return f"Response does not match the schema: {problem}.{pista} Return only valid JSON."


def _salvage_undeclared_only(schema: object, candidate: object) -> dict | None:
    """Devuelve ``candidate`` si sólo traía claves no declaradas y nada era obligatorio.

    Un esquema sin ``required`` no puede quedar insatisfecho por traer claves de más: se
    conserva el contenido en vez de convertirlo en una salida vacía. Devuelve ``None``
    cuando el esquema sí exige claves (ahí la falta es real) o cuando no hay datos.
    """
    if not isinstance(candidate, dict) or not candidate:
        return None
    if not isinstance(schema, dict) or not schema.get("properties"):
        return None
    if schema.get("required"):
        return None
    recortado, descartadas = _prune_to_schema(schema, candidate)
    if recortado not in ({}, []) or not descartadas:
        return None
    return candidate


def _schema_findings(dropped: list[str]) -> tuple[Finding, ...]:
    """Diagnóstico de las claves que el nodo devolvió de más y se descartaron."""
    if not dropped:
        return ()
    unicas = sorted(dict.fromkeys(dropped))
    return (
        Finding(
            severity=FindingSeverity.WARNING,
            message="Se ignoraron claves no declaradas en el output_schema: " + ", ".join(unicas),
            suggestion="Agrégalas al output_schema del agente si deben conservarse.",
        ),
    )


def _accumulate(total: Usage, part: Usage | None) -> Usage:
    if part is None:
        return total
    return Usage(
        prompt_tokens=total.prompt_tokens + part.prompt_tokens,
        completion_tokens=total.completion_tokens + part.completion_tokens,
        total_tokens=total.total_tokens + part.total_tokens,
    )


def _path_from_arguments(arguments: dict) -> str:
    for key in ("path", "file", "file_path", "out_path", "output", "target"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _context_to_text(context: ContextBundle) -> str:
    if not context.sections:
        return "(no context)"
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
