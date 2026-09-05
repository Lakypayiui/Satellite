"""Bridges the Satellite Python runtime to the web UI.

A run executes ``orchestrator.execute_goal`` on a worker thread and pushes
live events into a ``RunState`` (drained by HTTP polling or WebSocket). The
only monkeypatch is wrapping ``orchestrator.dispatch`` so each agent dispatch
(main step or complement chain) emits ``step_started``/``step_result`` — the
runtime itself is untouched.
"""

from __future__ import annotations

import importlib
import json as _json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from .. import store as store_mod
from ..context import ContextPreprocessor
from ..llm import llm_role_config
from ..orchestrator import execute_goal
from ..planner import Planner
from ..registry import AgentRegistry
from ..semantic import ContextCompressor, SemanticContextRefiner
from .run_state import RunState

_active: dict[str, RunState] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# proyecto / registros
# --------------------------------------------------------------------------

def project_root(root: str | None = None) -> Path:
    if root is None:
        root = os.getcwd()
    return Path(root).resolve()


def _store(root: Path) -> store_mod.AgentStore:
    return store_mod.AgentStore(str(root))


def _registry(root: Path) -> AgentRegistry:
    return _store(root).load_registry()


def _security(root: Path):
    from ..security import SecurityPolicy

    policy = SecurityPolicy()
    policy.load_defaults()
    config = _config(root)
    allow = (config or {}).get("security", {}).get("allow")
    if isinstance(allow, dict):
        for key, value in allow.items():
            policy.set_allowed(str(key), bool(value))
    return policy


def _config(root: Path) -> dict | None:
    path = root / ".satellite" / "config" / "config.json"
    if not path.exists():
        return None
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _host_bin() -> str | None:
    return os.getenv("SATELLITE_AGENT_HOST", "./build/satellite_agent_host")


def _catalog(registry: AgentRegistry) -> str:
    lines = ["Agentes disponibles:"]
    for agent in registry.list_agents():
        if not agent.enabled:
            continue  # bloqueados: fuera del catálogo del planner
        caps = ", ".join(agent.capabilities) or "-"
        lines.append(f"- id {agent.id}: {agent.name or agent.id} [{caps}]")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# grafo (para la pestaña Agentes)
# --------------------------------------------------------------------------

def build_graph(registry: AgentRegistry | None = None) -> dict[str, Any]:
    if registry is None:
        registry = _registry(project_root())
    capability_target: dict[str, int] = {}
    for agent in registry.list_agents():
        for cap in agent.capabilities:
            capability_target.setdefault(cap, agent.id)

    nodes = []
    for agent in registry.list_agents():
        nodes.append({
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "capabilities": list(agent.capabilities),
            "context_requirements": list(agent.context_requirements),
            "has_library": bool(agent.library_path),
            "is_native": not bool(agent.library_path),
            "enabled": agent.enabled,
            "complements": list(agent.complements),
        })

    edges = []
    for agent in registry.list_agents():
        for cap in agent.complements:
            target = capability_target.get(cap)
            if target is not None and target != agent.id:
                edges.append({"source": agent.id, "target": target, "capability": cap})
    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------
# dispatch observer (eventos en vivo sin tocar el runtime)
# --------------------------------------------------------------------------

def _wrap_dispatch(registry: AgentRegistry, emit):
    """Patch ``orchestrator.dispatch``; returns the original to restore with."""
    orch = importlib.import_module("satellite_py.orchestrator")
    dispatch_orig = orch.dispatch

    def wrapper(request: dict, *args, **kwargs):
        agent_id = request.get("agent_id")
        descriptor = registry.find_agent(int(agent_id)) if agent_id is not None else None
        name = descriptor.name if descriptor else f"id {agent_id}"
        routed_from = (request.get("metadata") or {}).get("routed_from")
        emit({
            "type": "step_started",
            "agent_id": agent_id,
            "name": name,
            "routed_from": routed_from,
        })
        result = dispatch_orig(request, *args, **kwargs)
        emit({
            "type": "step_result",
            "agent_id": agent_id,
            "name": name,
            "routed_from": routed_from,
            "status": result.get("status", "UNKNOWN"),
            "success": bool(result.get("status") == "SUCCESS"),
            "output": result.get("output"),
            "error": result.get("error"),
            "selection": (request.get("context") or {}).get("_selection"),
        })
        return result

    orch.dispatch = wrapper
    return dispatch_orig


# --------------------------------------------------------------------------
# ejecución de un run
# --------------------------------------------------------------------------

def _run_pipeline(state: RunState, root: Path, goal: str, resume: str | None, max_rounds: int) -> None:
    emit = state.emit
    orch = importlib.import_module("satellite_py.orchestrator")
    try:
        registry = _registry(root)
        security = _security(root)
        host_bin = _host_bin()
        data = _config(root)
        ctx_cfg = llm_role_config(data, "context")
        orch_cfg = llm_role_config(data, "orchestrator")
        ctx_client = ctx_cfg.create_client()
        orch_client = orch_cfg.create_client()

        emit({"type": "providers",
              "context": {"provider": ctx_cfg.provider, "model": ctx_cfg.model},
              "orchestrator": {"provider": orch_cfg.provider, "model": orch_cfg.model}})

        planner = Planner(orch_client, orch_cfg.provider)

        # Capa semántica del contexto con el rol "contexto".
        resolver = ContextPreprocessor(ctx_client, root, max_rounds=max_rounds)
        compressor = ContextCompressor(ctx_client)
        refiner = SemanticContextRefiner(ctx_client)

        # Pre-procesamiento: decide si falta información del usuario.
        result = resolver.preprocess(goal)
        emit({"type": "preprocess", "result": {
            "refined": result.refined_prompt,
            "needs_user_input": result.needs_user_input,
            "user_prompt": result.user_prompt,
            "missing": result.missing_info,
        }})
        if result.needs_user_input and not resume:
            emit({"type": "user_input_needed", "prompt": result.user_prompt})
            state.status = "awaiting_input"
            return

        # Compresión semántica a doc neutro (se pasa como contexto inicial);
        # con --resume no se re-comprime (se usa la sesión previa).
        neutral = None
        if result.refined_prompt and not resume:
            try:
                neutral = compressor.compress(goal, result.refined_prompt)
                emit({"type": "compressed", "context": neutral})
            except Exception as error:  # noqa: BLE001 - seguir sin capa semántica
                emit({"type": "compressed_fallback", "error": str(error)})

        emit({"type": "plan_start", "goal": goal})
        dispatch_orig = _wrap_dispatch(registry, emit)
        try:
            result_final = execute_goal(
                registry=registry,
                security=security,
                goal=goal,
                catalog_prompt=_catalog(registry),
                planner=planner,
                agent_host_bin=host_bin,
                context=neutral,
                project_root=str(root),
                session_dir=str(root / ".satellite" / "context"),
                max_rounds=max_rounds,
                resume_session=resume,
                context_client=ctx_client,
            )
        finally:
            orch.dispatch = dispatch_orig  # restore
        emit({"type": "done", "ok": bool(result_final.get("ok")), "summary": result_final.get("summary", "")})
        state.result = result_final
        state.status = "completed" if result_final.get("ok") else "failed"
    except Exception as error:  # noqa: BLE001
        state.error = str(error)
        emit({"type": "error", "error": str(error)})
        state.status = "error"
    finally:
        state.done.set()


def start_run(
    goal: str,
    root: str | None = None,
    resume: str | None = None,
    max_rounds: int = 3,
) -> str:
    root_path = project_root(root)
    run_id = uuid.uuid4().hex[:12]
    state = RunState(done=threading.Event())
    with _lock:
        _active[run_id] = state
    thread = threading.Thread(
        target=_run_pipeline,
        args=(state, root_path, goal, resume, int(max_rounds)),
        daemon=True,
    )
    state.thread = thread
    thread.start()
    return run_id


def _state(run_id: str) -> RunState | None:
    with _lock:
        return _active.get(run_id)


def get_run(run_id: str) -> dict | None:
    state = _state(run_id)
    if state is None:
        return None
    with state.lock:
        return {
            "run_id": run_id,
            "status": state.status,
            "events": list(state.events),
            "result": state.result,
            "error": state.error,
        }


def wait_done(run_id: str, timeout: float = 120.0) -> dict | None:
    state = _state(run_id)
    if state is None:
        return None
    state.done.wait(timeout=timeout)
    return get_run(run_id)


__all__ = [
    "project_root", "build_graph", "start_run", "get_run", "wait_done",
    "_registry", "_security", "_config",
]
