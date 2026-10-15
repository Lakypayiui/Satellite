"""FastAPI web console for Satellite — replica la apariencia del web legacy.

Static SPA (sidebar + chat + graph + files + settings) servida desde
``satellite/web/static``. El API expone el mismo core (Orchestrator), con
endpoints de archivos/proyecto/config propios de la consola.

Run::
    python -m satellite.web            # 127.0.0.1:7900 (uvicorn)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from satellite.approval.web import WebApproval
from satellite.cli.runtime_builder import build_runtime
from satellite.config import providers as provider_config
from satellite.config.models import fetch_models
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.persistence.store import FileStore
from satellite.web.runtime_manager import RuntimeManager

_HERE = Path(__file__).resolve().parent
_STATIC = _HERE / "static"

_IGNORED_DIRS = {
    ".git", ".satellite", "node_modules", "venv", ".venv", "build",
    "__pycache__", ".pytest_cache", ".commandcode", "dist", "out",
    "runtime", "web", "Satellite_OLD",
}
_MAX_CONTENT = 4_000  # max chars para "preguntar sobre archivo"


class RunRequest(BaseModel):
    goal: str


def create_app(
    project_root: Path | None = None,
    runtime: tuple | None = None,
    approval: object | None = None,
) -> FastAPI:
    """Build the console app bound to a project root (or a prebuilt runtime).

    runtime = (orchestrator, bus, store, registry) — used by tests.
    approval = ApprovalPort para el endpoint de aprobación humana.
    """
    if runtime is not None:
        orchestrator, bus, store, registry = runtime
        root = Path(project_root or Path.cwd()).resolve()
    else:
        root = Path(project_root or Path.cwd()).resolve()
        orchestrator, bus, store, registry = build_runtime(root)

    manager = RuntimeManager(orchestrator, bus)
    app = FastAPI(title="Satellite", version="1.0.0")
    app.state.approval = approval if approval is not None else WebApproval()
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    # ---------------- sistema / proyecto ----------------

    @app.get("/api/system")
    async def system():
        return {
            "root": str(root),
            "initialized": (root / ".satellite").exists(),
            "agents": len(registry.all()),
            "provider": None,
        }

    @app.get("/api/dirs")
    async def list_dirs(path: str = Query("", description="directorio a listar")):
        base = Path(path or root).expanduser().resolve()
        try:
            entries = [
                {"name": c.name, "path": str(c)}
                for c in sorted(base.iterdir(), key=lambda p: p.name.lower())
                if c.is_dir() and not c.name.startswith((".", "$"))
            ]
        except OSError as error:
            raise HTTPException(400, str(error))
        return {"path": str(base), "entries": entries}

    @app.post("/api/project/set")
    async def project_set(body: dict[str, Any]):
        candidate = Path(str(body.get("path", "") or "")).expanduser()
        if not candidate.is_dir():
            raise HTTPException(400, f"no existe: {candidate}")
        global _ACTIVE_ROOT
        _ACTIVE_ROOT = candidate.resolve()
        return {"ok": True, "root": str(_ACTIVE_ROOT)}

    # ---------------- agentes (grafo de specs) ----------------

    @app.get("/api/agents")
    async def agents_graph():
        nodes = []
        for spec in registry.all():
            nodes.append({
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "version": "0.2.0",
                "is_native": True,
                "enabled": True,
                "capabilities": list(spec.capabilities),
                "tools": list(spec.tools),
            })
        return {"nodes": nodes, "edges": []}

    @app.get("/api/agents/{agent_id}")
    async def agent_detail(agent_id: str):
        try:
            spec = registry.get(agent_id)
        except KeyError:
            raise HTTPException(404, f"agente {agent_id} no existe")
        return spec.to_dict()

    # ---------------- archivos ----------------

    @app.get("/api/files")
    async def files_tree(path: str = Query(".", description="directorio relativo")):
        base = (root / path).resolve()
        if base != root and root not in base.parents:
            raise HTTPException(403, "fuera del proyecto")
        if not base.is_dir():
            raise HTTPException(404, "directorio no existe")
        entries: list[dict[str, Any]] = []
        for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.is_dir():
                if child.name in _IGNORED_DIRS:
                    continue
                entries.append({"name": child.name, "type": "dir", "path": str(child.relative_to(root))})
            else:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append({
                    "name": child.name, "type": "file", "path": str(child.relative_to(root)),
                    "size": size, "ext": child.suffix.lstrip("."),
                })
        return {"path": str(base.relative_to(root)) if base != root else ".", "entries": entries}

    @app.get("/api/file")
    async def file_read(path: str = Query(..., description="ruta relativa")):
        full = (root / path).resolve()
        if full != root and root not in full.parents:
            raise HTTPException(403, "fuera del proyecto")
        if not full.is_file():
            raise HTTPException(404, "archivo no existe")
        try:
            content = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise HTTPException(400, "no es un archivo de texto")
        return {"path": path, "content": content}

    @app.put("/api/file")
    async def file_write(body: dict[str, Any]):
        path = str(body.get("path", ""))
        content = str(body.get("content", ""))
        full = (root / path).resolve()
        if full != root and root not in full.parents:
            raise HTTPException(403, "fuera del proyecto")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path}

    @app.post("/api/file/ask")
    async def file_ask(body: dict[str, Any]):
        """Responde preguntas sobre un archivo con el proveedor configurado."""
        path = str(body.get("path", ""))
        question = str(body.get("question", "")).strip()
        if not path or not question:
            raise HTTPException(400, "faltan 'path' y 'question'")
        full = (root / path).resolve()
        if full != root and root not in full.parents:
            raise HTTPException(403, "fuera del proyecto")
        if not full.is_file():
            raise HTTPException(404, "archivo no existe")
        try:
            content = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise HTTPException(400, "no es un archivo de texto")
        return {
            "path": path,
            "question": question,
            "answer": f"(pregunta pendiente — el proveedor LLM responde aquí; contenido de {path} recortado)",
        }

    # ---------------- configuración ----------------

    @app.get("/api/config")
    async def config_get():
        return provider_config.load_config(root)

    @app.get("/api/config/providers")
    async def config_providers():
        """Proveedores guardados (sin api_key) para el selector y la tabla."""
        return {
            "global": provider_config.active_public(root),
            "saved": [p.to_dict() for p in provider_config.list_providers(root)],
            "global_id": provider_config.active_id(root),
        }

    @app.post("/api/config/providers")
    async def config_providers_save(body: dict[str, Any]):
        """Guarda un proveedor en la lista de guardados y lo deja como global.

        - Cada proveedor lleva un ``id`` único generado al guardar (uuid).
        - Si ya existe EXACTAMENTE el mismo (name+model+base_url), se reutiliza
          su id (no se duplica); si hay uno igual (name+model+base_url) pero
          cambia algo, se crea ENTRE (permite 2 modelos con la misma URL).
        - api_key previa con mismo name+model+base_url se conserva si no llega nueva.
        - Actualiza la seccion ``llm`` (global activo) con el recién guardado.
        """
        provider_config.save_provider(
            root,
            name=str(body.get("name", "")),
            model=str(body.get("model", "")),
            base_url=str(body.get("base_url", "")),
            api_key=str(body.get("api_key", "") or ""),
            api_key_env=str(body.get("api_key_env", "")),
        )
        return {"ok": True, "providers": [
            {"id": p.id, "name": p.name, "model": p.model,
             "base_url": p.base_url, "api_key_set": p.api_key_set}
            for p in provider_config.list_providers(root)
        ]}

    @app.put("/api/config/providers/{provider_id}")
    async def config_providers_update(provider_id: str, body: dict[str, Any]):
        """Actualiza un proveedor guardado (por id). api_key vacío = no cambiar."""
        try:
            provider_config.update_provider(
                root,
                provider_id,
                name=str(body["name"]) if body.get("name") is not None else None,
                model=str(body["model"]) if body.get("model") is not None else None,
                base_url=str(body["base_url"]) if body.get("base_url") is not None else None,
                api_key_env=str(body["api_key_env"]) if body.get("api_key_env") is not None else None,
                api_key=str(body.get("api_key", "") or ""),
            )
        except KeyError as exc:
            raise HTTPException(404, f"proveedor no encontrado: {provider_id}") from exc
        return {"ok": True}

    @app.delete("/api/config/providers/{provider_id}")
    async def config_providers_delete(provider_id: str):
        """Elimina un proveedor guardado (por id)."""
        if not provider_config.delete_provider(root, provider_id):
            raise HTTPException(404, f"proveedor no encontrado: {provider_id}")
        return {"ok": True}

    @app.get("/api/models")
    async def list_models(base_url: str = Query("", description="URL del proveedor"), provider: str = Query("openai")):
        """Devuelve los modelos compatibles con la URL (OpenAI-compat o Anthropic)."""
        api_key = provider_config.api_key_for_base_url(root, base_url) or None
        if not api_key:
            env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
            api_key = os.getenv(env)
        result = fetch_models(base_url, provider, api_key)
        if not result.ok:
            return {"ok": False, "error": result.error, "models": [], "base_url": result.url}
        payload: dict[str, Any] = {
            "ok": True, "models": list(result.models), "base_url": result.url
        }
        if result.error:
            payload["error"] = result.error
        return payload

    @app.put("/api/config/roles")
    async def config_roles_put(body: dict[str, Any]):
        """Guarda la configuración de roles (por rol individual)."""
        roles = body.get("roles")
        if not isinstance(roles, dict):
            raise HTTPException(400, "falta 'roles'")
        try:
            provider_config.set_roles(root, roles)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/config/preprocess")
    async def config_preprocess(body: dict[str, Any]):
        """Activa/desactiva el refinamiento del goal (refine_goal)."""
        cfg_path = root / ".satellite" / "config" / "config.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        enabled = bool(body.get("enabled"))
        data["refine_goal"] = enabled
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "refine_goal": enabled}

    @app.get("/api/observability")
    async def observability():
        """Registro de ejecuciones observables (últimas 100)."""
        from satellite.observability.logger import ExecutionLogger

        logger = ExecutionLogger(root / ".satellite" / "observability")
        records = logger.load_all()
        return {"count": len(records), "records": [r.to_dict() for r in records[-100:]]}

    @app.get("/api/config/roles")
    async def config_roles_list():
        return {"providers": ["local", "openai", "openai-compatible", "deepseek", "anthropic"]}

    @app.get("/api/stats")
    async def stats_report():
        """Métricas de skills (línea base marcada como estimación) + cache LLM."""
        from satellite.kernel.paths import resolve_paths
        from satellite.metrics.cache_report import load_cache_report
        from satellite.metrics.stats import build_report
        from satellite.skills.registry import SkillRegistry

        skills_root = resolve_paths(root).skills_root
        registry = SkillRegistry(skills_root)
        registry.load_all()
        cache = load_cache_report(root)
        # [AJ-8] coste por uso real (con cache), no el hipotético
        return build_report(
            registry.all(),
            call_cost_tokens=int(cache.real_tokens_per_call or 0),
            cache=cache.to_dict(),
        ).to_dict()

    # ---------------- ejecución ----------------

    @app.post("/api/run")
    async def run_start(body: RunRequest):
        goal = body.goal.strip()
        if not goal:
            raise HTTPException(400, "falta 'goal'")
        task_id = manager.start(goal, root)
        return {"run_id": task_id, "status": "running"}

    @app.get("/api/run/{run_id}")
    async def run_poll(run_id: str):
        events, _ = manager.events_since(run_id, 0)
        states = [e.event_type.value for e in events]
        status = "completed" if "task_finished" in states else ("running" if states else "starting")
        final = manager.result(run_id)
        summary = ""
        ok = status == "completed"
        output = None
        err = None
        # Si terminó, entregar la respuesta real (TaskResult): summary = respuesta
        if isinstance(final, dict):
            summary = final.get("error", "") or "Error"
            ok = False
            err = final.get("error")
        elif final is not None:
            summary = getattr(final, "summary", "") or ""
            ok = getattr(final, "status", None) is not None and getattr(final, "status").value == "ok"
            output = getattr(final, "output", None) or None
            err = getattr(final, "error", None)
        return {
            "run_id": run_id,
            "status": status,
            "events": [e.to_dict() for e in events],
            "result": {
                "ok": ok,
                "summary": summary,
                "output": output,
                "error": err,
            },
        }

    @app.get("/api/tasks")
    async def tasks_list():
        return {"tasks": store.list_tasks()}

    # ---------------- aprobación ----------------

    @app.get("/api/approvals")
    async def approvals_pending():
        port = getattr(app.state, "approval", None)
        pending = port.pending() if port is not None and hasattr(port, "pending") else []
        return {"pending": pending}

    @app.post("/api/run/{run_id}/approve")
    async def run_approve(run_id: str, body: dict[str, Any]):
        """Resuelve una propuesta pendiente (modal) y reanuda la tarea."""
        port = getattr(app.state, "approval", None)
        if port is None or not hasattr(port, "submit"):
            raise HTTPException(400, "no hay puerto de aprobación configurado")
        try:
            status = ApprovalStatus(str(body.get("status", "")).lower())
        except ValueError:
            raise HTTPException(400, f"status inválido: {body.get('status')!r}")
        decision = ApprovalDecision(
            status=status,
            reason=str(body.get("reason", "")),
            feedback=str(body.get("feedback", "")),
        )
        proposal_id = str(body.get("proposal_id") or run_id)
        accepted = port.submit(proposal_id, decision)
        return {"ok": True, "accepted": bool(accepted), "status": status.value}

    # ---------------- estático ----------------

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    return app


_ACTIVE_ROOT: Path | None = None


def get_app(project_root: Path | None = None) -> FastAPI:
    """Get (or create) the default app instance (never fails on import)."""
    try:
        return create_app(project_root)
    except Exception:
        minimal = FastAPI(title="Satellite (sin proveedor)")

        @minimal.get("/api/system")
        async def _system_placeholder():
            return {"error": "no hay proveedor LLM configurado: usa 'satellite config'"}

        return minimal


app = get_app()
