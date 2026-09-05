"""Plan execution for the Python Satellite runtime."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from .context import ContextPreprocessor
from .dispatcher import dispatch
from .planner import Plan, Planner
from .registry import AgentRegistry
from .security import SecurityPolicy
from .semantic import ContextCompressor

# Umbral de re-compresión por defecto (chars de output nuevo acumulado).
_RECOMPRESS_THRESHOLD = int(os.getenv("SATELLITE_RECOMPRESS_CHARS", "60000"))


def _default_host_bin() -> str:
    return os.getenv("SATELLITE_AGENT_HOST", "./build/satellite_agent_host")


def _step_context(
    step_context: dict[str, Any],
    step_description: str,
    resolver: ContextPreprocessor | None,
    refiner: Any | None,
    compressed: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the neutral context dict sent to one agent request.

    Two filters in series for the project context:

        1. ``optimizer.optimize`` — deterministic keyword/token filter
           (no LLM) that selects candidate files for this step.
        2. ``refiner.refine`` — the semantic layer (same model that
           interprets the request) keeps only what the intention needs.

    The run's compressed context (intention/constraints/status) is merged on
    top. When the step declares explicit paths/symbols (planner-declared),
    they are resolved directly instead of running the two filters.
    """
    merged: dict[str, Any] = {}
    if compressed:
        merged["intention"] = compressed.get("intention", "")
        merged["constraints"] = compressed.get("constraints", [])
        merged["status"] = compressed.get("status", "")

    declared_paths = (
        step_context.get("paths")
        if isinstance(step_context, dict) and isinstance(step_context.get("paths"), list)
        else None
    )
    if resolver is not None and declared_paths:
        declared_symbols = (
            step_context.get("symbols")
            if isinstance(step_context, dict) and isinstance(step_context.get("symbols"), list)
            else []
        )
        content = resolver.resolve(declared_paths, declared_symbols or None)
        if content:
            merged["project"] = content
        merged["_paths_used"] = declared_paths
        return merged

    # Pipeline de selección (solo cuando el planner no declaró paths explícitos).
    if resolver is not None:
        from . import optimizer as optimizer_module

        files = resolver.index_files()
        if files:
            task_text = step_description or merged.get("intention", "")
            selection, stats = optimizer_module.optimize(
                description=task_text,
                files=files,
                token_budget=int(os.getenv("SATELLITE_TOKEN_BUDGET", "4000")),
            )
            merged["_selection"] = {
                "files": selection.selected_files,
                "symbols": selection.selected_symbols,
                "estimated_tokens": selection.estimated_tokens,
                "relevance_score": round(stats.relevance_score, 4),
                "compression_ratio": round(stats.compression_ratio, 4),
                "tokens_saved": stats.tokens_saved,
            }
            keep_files = selection.selected_files
            keep_symbols = selection.selected_symbols
            if refiner is not None:
                refined = refiner.refine(
                    task_text,
                    selection.selected_files,
                    selection.selected_symbols,
                )
                keep_files = refined.get("keep_files") or keep_files
                keep_symbols = refined.get("keep_symbols") or keep_symbols
                merged["_selection"]["removed"] = refined.get("remove_reason", {})
            content = resolver.resolve(keep_files, keep_symbols or None)
            if content:
                merged["project"] = content
            merged["_paths_used"] = keep_files
    return merged


def _refresh_stale_step_context(request_context: dict[str, Any], resolver: ContextPreprocessor) -> list[str]:
    """Re-read project content that changed on disk since the index was built.

    Triggered before each plan step: files the step is about to use may have
    been modified by a previous step (the C++ mtime invalidation, but between
    plan steps instead of between builds). Returns the stale paths refreshed.
    """
    paths_used = request_context.get("_paths_used") or []
    if not paths_used:
        return []
    stale_check = getattr(resolver, "stale_paths", None)
    refresh_entry = getattr(resolver, "refresh_index_entry", None)
    if not callable(stale_check) or not callable(refresh_entry):
        return []  # resolver sin soporte de invalidación: no refrescar
    try:
        stale = stale_check(paths_used)
    except OSError:
        return []
    if not stale:
        return []
    for path in stale:
        refresh_entry(path)
    # Re-resolver con el índice actualizado devuelve el contenido fresco.
    content = resolver.resolve(stale)
    if content and "project" in request_context:
        request_context["project"] = content
    elif content:
        request_context["project"] = content
    return stale


def _find_complement(
    registry: AgentRegistry,
    descriptor: Any,
) -> Any | None:
    """Return the first enabled agent whose capability matches ``complements``."""
    for complement_cap in descriptor.complements:
        for candidate in registry.list_agents():
            if not candidate.enabled:
                continue
            if complement_cap in candidate.capabilities:
                return candidate
    return None


def _log_execution(
    execution_logger: Any,
    agent_id: int,
    agent_name: str,
    agent_version: str,
    input_payload: dict[str, Any],
    request_context: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Write one execution record (best-effort)."""
    from .execution_log import ExecutionRecord

    selection_meta = (request_context or {}).get("_selection") or {}
    record = ExecutionRecord(
        agent_id=agent_id,
        agent_name=agent_name,
        agent_version=agent_version,
        input=input_payload,
        context={k: v for k, v in (request_context or {}).items() if not k.startswith("_")},
        output=result.get("output"),
        duration_ms=float(result.get("duration_ms") or 0.0),
        status=str(result.get("status")),
        error_message=str(result.get("error") or ""),
        tokens_after=int(selection_meta.get("estimated_tokens") or 0),
        tokens_saved=int(selection_meta.get("tokens_saved") or 0),
        compression_ratio=float(selection_meta.get("compression_ratio") or 0.0),
        relevance_score=float(selection_meta.get("relevance_score") or 0.0),
    )
    execution_logger.log(record)


# Profundidad máxima de una cadena directa de complementos (evita ciclos).
_MAX_COMPLEMENT_CHAIN = int(os.getenv("SATELLITE_MAX_COMPLEMENT_CHAIN", "8"))


def run_plan(
    registry: AgentRegistry,
    security: SecurityPolicy,
    plan: Plan,
    agent_host_bin: str | None = None,
    context: dict[str, Any] | None = None,
    resolver: ContextPreprocessor | None = None,
    refiner: Any | None = None,
    compressor: ContextCompressor | None = None,
    session_path: str | None = None,
    resumed_from: str = "",
    execution_logger: Any | None = None,
    auto_expand: bool = False,
    expand_client: Any = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Execute a validated plan in topological order, stopping at first failure.

    Each step receives the context its planner declared (resolved against the
    project index) plus the run's compressed neutral context. Successful step
    outputs are appended to the run context and re-compressed when they grow
    past the threshold. The neutral session document is persisted to
    ``session_path`` (``.satellite/context/session_<id>.json``).
    """
    planner = Planner()
    try:
        order = planner.execution_order(plan)
    except ValueError as error:
        return {"ok": False, "results": [], "summary": str(error)}

    host_bin = agent_host_bin or _default_host_bin()
    run_goal = plan.goal
    compressed: dict[str, Any] | None = context  # contexto comprimido inicial
    raw_outputs: list[str] = []
    session: dict[str, Any] = {
        "schema": "satellite/context-session/1",
        "session_id": str(uuid.uuid4())[:8],
        "created_ms": int(time.time() * 1000),
        "goal": run_goal,
        "provider_agnostic": True,
        "steps": [],
        "resumed_from": resumed_from,
    }
    results: dict[int, dict[str, Any]] = {}
    summary: list[str] = []
    for index in order:
        step = plan.steps[index]
        refreshed: list[str] = []
        request_context: dict[str, Any] = {}
        failed_dependency = next(
            (
                dependency
                for dependency in step.dependencies
                if results.get(dependency, {}).get("status") != "SUCCESS"
            ),
            None,
        )
        if failed_dependency is not None:
            result = {
                "agent_id": step.agent_id,
                "status": "FAILED",
                "error": "dependency failed",
            }
        else:
            request_context = _step_context(
                step.context or {},
                step.description,
                resolver,
                refiner,
                compressed,
            )
            # Refresco por mtime entre pasos: si un paso previo modificó algún
            # archivo que este paso va a usar, releer el contenido fresco.
            if resolver is not None:
                refreshed = _refresh_stale_step_context(request_context, resolver)
                request_context.pop("_paths_used", None)
            request = {
                "agent_id": step.agent_id,
                "input": step.input,
                "context": request_context,
                "metadata": {"description": step.description},
            }
            result = dispatch(
                registry,
                security,
                request,
                host_bin,
                auto_expand=auto_expand,
                goal=run_goal,
                expand_client=expand_client,
                project_root=project_root,
            )

        results[index] = result
        descriptor = registry.find_agent(step.agent_id)
        name = descriptor.name if descriptor else f"id_{step.agent_id}"
        note = f" (contexto refrescado: {', '.join(refreshed)})" if refreshed else ""
        summary.append(f"paso {index}: agent {name} -> {result.get('status')}{note}")
        session["steps"].append(
            {
                "index": index,
                "agent_id": step.agent_id,
                "agent_name": name,
                "description": step.description,
                "status": result.get("status"),
                "output": result.get("output"),
                "error": result.get("error"),
                "context_refreshed": refreshed,
            }
        )
        # Observabilidad: log por ejecución en .satellite/executions/.
        if execution_logger is not None and request_context is not None:
            _log_execution(
                execution_logger,
                step.agent_id,
                name,
                descriptor.version if descriptor else "",
                step.input,
                request_context,
                result,
            )
        if result.get("status") == "SUCCESS" and result.get("output") is not None:
            output_text = json.dumps(result["output"], ensure_ascii=False)
            raw_outputs.append(f"paso {index} ({name}): {output_text}")
            session.setdefault("step_outputs", {})[str(index)] = result["output"]

        # Red de complementos: si el agente declara complementos y tuvo éxito,
        # su output se encadena DIRECTAMENTE al complemento (sin volver al
        # orquestador). Si el complemento falla, el error vuelve al orquestador
        # (routed_to_orchestrator) y la corrida corta.
        routed_ok = True
        if result.get("status") == "SUCCESS" and descriptor is not None and descriptor.complements:
            emitter_output = result.get("output")
            complement = _find_complement(registry, descriptor)
            chain_depth = 0
            current_agent = complement
            current_input = emitter_output
            routed_from_index = index
            visited: set[int] = set()
            while (
                current_agent is not None
                and current_input is not None
                and chain_depth < _MAX_COMPLEMENT_CHAIN
                and current_agent.id not in visited
            ):
                visited.add(current_agent.id)
                chain_depth += 1
                chain_request_context = _step_context(
                    {},
                    f"complemento de paso {routed_from_index} ({name})",
                    resolver,
                    refiner,
                    compressed,
                )
                if resolver is not None:
                    _refresh_stale_step_context(chain_request_context, resolver)
                    chain_request_context.pop("_paths_used", None)
                chain_request = {
                    "agent_id": current_agent.id,
                    "input": current_input,
                    "context": chain_request_context,
                    "metadata": {
                        "routed_from": routed_from_index,
                        "description": f"complemento de paso {routed_from_index}",
                    },
                }
                chain_result = dispatch(
                    registry,
                    security,
                    chain_request,
                    host_bin,
                    auto_expand=auto_expand,
                    goal=run_goal,
                    expand_client=expand_client,
                    project_root=project_root,
                )
                chain_name = current_agent.name or f"id_{current_agent.id}"
                summary.append(
                    f"paso {index}.{chain_depth}: agent {chain_name} -> "
                    f"{chain_result.get('status')} (ruteado desde paso {routed_from_index})"
                )
                session["steps"].append(
                    {
                        "index": index,
                        "sub": chain_depth,
                        "agent_id": current_agent.id,
                        "agent_name": chain_name,
                        "status": chain_result.get("status"),
                        "output": chain_result.get("output"),
                        "error": chain_result.get("error"),
                        "routed_from": routed_from_index,
                        "routed_to_orchestrator": chain_result.get("status") != "SUCCESS",
                    }
                )
                if execution_logger is not None:
                    _log_execution(
                        execution_logger,
                        current_agent.id,
                        chain_name,
                        current_agent.version,
                        current_input if isinstance(current_input, dict) else {"value": current_input},
                        chain_request_context,
                        chain_result,
                    )
                if chain_result.get("status") != "SUCCESS":
                    chain_result["routed_to_orchestrator"] = True
                    result = chain_result
                    routed_ok = False
                    break
                if chain_result.get("output") is not None:
                    output_text = json.dumps(chain_result["output"], ensure_ascii=False)
                    raw_outputs.append(f"paso {index}.{chain_depth} ({chain_name}): {output_text}")
                # Continuar la cadena con el complemento del eslabón actual.
                current_input = chain_result.get("output")
                routed_from_index = current_agent.id
                current_agent = (
                    _find_complement(registry, current_agent)
                    if current_agent.complements
                    else None
                )
            if not routed_ok:
                # El paso efectivo terminó en el complemento fallido: el
                # resultado del paso refleja el fallo para el orquestador.
                results[index] = result
                _persist_session(session_path, session)
                return {
                    "ok": False,
                    "results": [results[item] for item in order if item in results],
                    "summary": "\n".join(summary),
                }
        if compressor is not None and sum(len(o) for o in raw_outputs) > _RECOMPRESS_THRESHOLD:
            raw = "\n".join(raw_outputs)
            raw_outputs.clear()
            compressed = compressor.compress(run_goal, raw, previous=compressed)
            session["last_compression"] = compressed

        if result.get("status") != "SUCCESS":
            _persist_session(session_path, session)
            return {
                "ok": False,
                "results": [results[item] for item in order if item in results],
                "summary": "\n".join(summary),
            }

    session["ok"] = True
    session["final_context"] = compressed
    _persist_session(session_path, session)
    return {
        "ok": True,
        "results": [results[item] for item in order],
        "summary": "\n".join(summary),
        "session": session_path or "",
    }


def _persist_session(session_path: str | None, session: dict[str, Any]) -> None:
    """Write the neutral session JSON (Satellite-owned, provider-agnostic).

    E6: the document is sanitized on write — only the neutral vocabulary is
    persisted, never provider memory.
    """
    if not session_path:
        return
    from .context_schema import persist_session as _persist

    _persist(session_path, session)


def execute_goal(
    registry: AgentRegistry,
    security: SecurityPolicy,
    goal: str,
    catalog_prompt: str,
    planner: Planner,
    agent_host_bin: str | None = None,
    context: dict[str, Any] | None = None,
    project_root: str | None = None,
    session_dir: str | None = None,
    max_rounds: int = 3,
    resume_session: str | None = None,
    context_client: Any = None,
    auto_expand: bool = False,
    expand_client: Any = None,
) -> dict[str, Any]:
    """Plan a goal with the LLM and execute it through the dispatcher.

    ``project_root``/``session_dir`` enable per-step context resolution and
    neutral session persistence (Satellite-owned JSON). ``resume_session``
    points to a previous ``session_*.json``; its compressed final context is
    loaded (E6 neutral vocabulary) and used as the initial run context so a
    later run can keep working without rebuilding anything.
    """
    initial_context = context
    resumed_from = ""
    if resume_session:
        from .context_schema import load_session

        previous = load_session(resume_session)
        final_context = previous.get("final_context")
        if isinstance(final_context, dict):
            initial_context = final_context
            resumed_from = resume_session

    try:
        plan = planner.plan_goal(goal, catalog_prompt)
    except (RuntimeError, ValueError, KeyError) as error:
        return {"ok": False, "results": [], "summary": str(error)}

    # Respuesta directa del orquestador (plan sin pasos): tareas de lectura/
    # análisis que el LLM responde con el contexto disponible, sin agentes.
    if not plan.steps and plan.answer:
        if session_dir is not None:
            from pathlib import Path

            from .context_schema import SESSION_SCHEMA, persist_session

            session_dir_path = Path(session_dir)
            session_dir_path.mkdir(parents=True, exist_ok=True)
            persist_session(
                os.fspath(session_dir_path / f"session_{int(time.time() * 1000)}.json"),
                {
                    "schema": SESSION_SCHEMA,
                    "session_id": str(uuid.uuid4())[:8],
                    "created_ms": int(time.time() * 1000),
                    "goal": goal,
                    "provider_agnostic": True,
                    "steps": [],
                    "answer": plan.answer,
                },
            )
        return {"ok": True, "results": [], "summary": plan.answer, "answer": plan.answer}

    resolver: ContextPreprocessor | None = None
    refiner: Any | None = None
    compressor: ContextCompressor | None = None
    session_path: str | None = None
    execution_logger: Any | None = None
    if project_root is not None:
        from pathlib import Path

        from .execution_log import ExecutionLogger
        from .llm import load_llm_config
        from .semantic import SemanticContextRefiner

        root = Path(project_root)
        config_path = root / ".satellite" / "config" / "config.json"
        client = context_client
        if client is None:
            try:
                client = load_llm_config(config_path).create_client()
            except Exception:  # noqa: BLE001 - sin proveedor no hay capa semántica
                client = None
        resolver = ContextPreprocessor(
            client=client or planner.client,
            project_root=root,
            max_rounds=max_rounds,
        )
        if client is not None:
            compressor = ContextCompressor(client)
            refiner = SemanticContextRefiner(client)
        if session_dir:
            session_dir_path = Path(session_dir)
            session_dir_path.mkdir(parents=True, exist_ok=True)
            session_path = os.fspath(
                session_dir_path / f"session_{int(time.time() * 1000)}.json"
            )
        execution_logger = ExecutionLogger(str(root / ".satellite" / "executions"))

    # Auto-expansión: si durante el run se crearon agentes nuevos, persistirlos.
    known_ids = {a.id for a in registry.list_agents()}

    result = run_plan(
        registry,
        security,
        plan,
        agent_host_bin,
        context=initial_context,
        resolver=resolver,
        refiner=refiner,
        compressor=compressor,
        session_path=session_path,
        resumed_from=resumed_from,
        execution_logger=execution_logger,
        auto_expand=auto_expand,
        expand_client=expand_client,
        project_root=project_root,
    )

    if auto_expand and project_root is not None:
        from pathlib import Path

        from .store import AgentStore

        new_ids = {a.id for a in registry.list_agents()} - known_ids
        if new_ids:
            AgentStore(str(Path(project_root))).save_registry(registry)
    return result
