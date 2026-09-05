"""FastAPI backend for the Satellite web UI.

Static SPA served from ``web/static``; JSON/streaming API in ``/api``.
The runtime bridge lives in ``web/runner`` (events, graph, runs).

Run with::

    python -m satellite_py.web            # localhost:7900 (uvicorn)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..context import ContextPreprocessor
from . import runner
from .runner import _config, _registry, _security, project_root

_HERE = Path(__file__).resolve().parent
_STATIC = _HERE / "static"
ROLES = ("context", "orchestrator", "agents")

app = FastAPI(title="Satellite Web", version="1.0.0")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

# Raíz de proyecto activa (mutable desde la UI: "abrir carpeta").
_ROOT: str | None = None


def _root() -> Path:
    if _ROOT:
        return project_root(_ROOT)
    return project_root(os.getenv("SATELLITE_WEB_ROOT") or os.getcwd())


def _need_store(root: Path):
    store = runner._store(root)
    if not store.has_state():
        raise HTTPException(400, "proyecto no inicializado (falta .satellite/)")
    return store


# --------------------------------------------------------------------------
# sistema / contexto
# --------------------------------------------------------------------------

@app.get("/api/system")
def system():
    root = _root()
    store = runner._store(root)
    initialized = store.has_state()
    provider: dict[str, str] | None = None
    config = _config(root)
    if config:
        try:
            from ..llm import load_llm_config

            cfg = load_llm_config(root / ".satellite" / "config" / "config.json")
            provider = {"provider": cfg.provider, "model": cfg.model}
        except Exception:
            provider = None
    agents = len(_registry(root).list_agents()) if initialized else 0
    return {"root": str(root), "initialized": initialized, "provider": provider, "agents": agents}


@app.get("/api/dirs")
def list_dirs(path: str = Query("", description="directorio absoluto a listar")):
    """Lista directorios del sistema para 'abrir carpeta' desde la UI."""
    base = Path(path) if path else Path.home()
    try:
        entries = []
        for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            if child.name.startswith((".", "$")):
                continue
            entries.append({"name": child.name, "path": str(child)})
        return {"path": str(base), "entries": entries}
    except OSError as error:
        raise HTTPException(400, str(error))


@app.post("/api/project/set")
def project_set(body: dict[str, Any]):
    """Cambia la raíz de proyecto activa (abrir carpeta desde la UI)."""
    global _ROOT
    candidate = Path(str(body.get("path", "") or "")).expanduser()
    if not candidate.is_dir():
        raise HTTPException(400, f"no existe: {candidate}")
    _ROOT = str(candidate.resolve())
    return {"ok": True, "root": _ROOT}


@app.post("/api/project/init")
def project_init():
    """Inicializa el proyecto activo (`.satellite/` + registry + config)."""
    root = _root()
    store = runner._store(root)
    store.initialize()
    return {"ok": True, "root": str(root), "initialized": True}


@app.get("/api/context/index")
def context_index():
    root = _root()
    resolver = ContextPreprocessor()
    resolver.project_root = root
    files = resolver.index_files()
    total = sum(int(f.get("size", 0)) for f in files)
    return {"files": files, "count": len(files), "total_bytes": total}


# --------------------------------------------------------------------------
# agentes (grafo + bloqueo)
# --------------------------------------------------------------------------

@app.get("/api/agents")
def agents_graph():
    root = _root()
    _need_store(root)
    return runner.build_graph(_registry(root))


@app.get("/api/agents/{agent_id}")
def agent_detail(agent_id: int):
    root = _root()
    registry = _registry(root)
    agent = registry.find_agent(agent_id)
    if agent is None:
        raise HTTPException(404, f"agente {agent_id} no existe")
    return {
        "id": agent.id, "name": agent.name, "description": agent.description,
        "version": agent.version, "input_schema": agent.input_schema,
        "output_schema": agent.output_schema,
        "context_requirements": agent.context_requirements,
        "capabilities": agent.capabilities, "library_path": agent.library_path,
        "is_native": not bool(agent.library_path), "enabled": agent.enabled,
        "complements": agent.complements,
    }


@app.post("/api/agents/{agent_id}/enabled")
def set_agent_enabled(agent_id: int, body: dict[str, Any]):
    enabled = bool(body.get("enabled"))
    root = _root()
    store = _need_store(root)
    registry = store.load_registry()
    if not registry.set_enabled(agent_id, enabled):
        raise HTTPException(404, f"agente {agent_id} no existe")
    store.save_registry(registry)
    return {"ok": True, "id": agent_id, "enabled": enabled}


# --------------------------------------------------------------------------
# archivos (explorador + editor)
# --------------------------------------------------------------------------

_IGNORED_DIRS = {
    ".git", ".satellite", "node_modules", "venv", ".venv", "build",
    "build-make", "build2", "build_vs", "__pycache__", ".pytest_cache",
    ".commandcode", "third_party", ".idea", "dist", "out",
}


@app.get("/api/files")
def files_tree(path: str = Query(".", description="directorio relativo")):
    root = _root()
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
                "size": size,
                "ext": child.suffix.lstrip("."),
            })
    return {"path": str(base.relative_to(root)) if base != root else ".", "entries": entries}


@app.get("/api/file")
def file_read(path: str = Query(..., description="ruta relativa")):
    root = _root()
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
def file_write(body: dict[str, Any]):
    path = str(body.get("path", ""))
    content = str(body.get("content", ""))
    root = _root()
    full = (root / path).resolve()
    if full != root and root not in full.parents:
        raise HTTPException(403, "fuera del proyecto")
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return {"ok": True, "path": path}


# --------------------------------------------------------------------------
# configuración (menú de modelos por rol)
# --------------------------------------------------------------------------

_PROVIDERS = ["local", "openai", "openai-compatible", "deepseek", "anthropic"]


@app.get("/api/config")
def config_get():
    root = _root()
    return _config(root) or {}


@app.get("/api/config/roles")
def config_roles():
    root = _root()
    data = _config(root) or {}
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    out: dict[str, Any] = {"providers": _PROVIDERS, "global": {}, "roles": {}}
    out["global"] = {
        "provider": llm.get("provider", ""), "model": llm.get("model", ""),
        "base_url": llm.get("base_url", ""), "api_key_env": llm.get("api_key_env", ""),
        "api_key_set": bool(llm.get("api_key") or os.getenv(str(llm.get("api_key_env") or ""), "")),
    }
    for role in ROLES:
        section = llm.get(role) if isinstance(llm.get(role), dict) else {}
        out["roles"][role] = {
            "provider": section.get("provider", llm.get("provider", "")),
            "model": section.get("model", llm.get("model", "")),
            "base_url": section.get("base_url", llm.get("base_url", "")),
            "api_key_env": section.get("api_key_env", llm.get("api_key_env", "")),
            "override": bool(section),
        }
    return out


@app.put("/api/config/roles")
def config_roles_put(body: dict[str, Any]):
    """Persiste los selectores de provider/credenciales por rol (o global).

    Cada sección de rol admite: provider, model, base_url, api_key (literal),
    api_key_env (nombre de variable de entorno) o ``clear`` para quitar el
    override del rol. Si ``roles.global`` viene, actualiza la sección ``llm``
    raíz. Las claves se guardan en el config.json del proyecto (nunca se
    devuelven por GET).
    """
    root = _root()
    path = root / ".satellite" / "config" / "config.json"
    data = _config(root) or {}
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    roles = body.get("roles")
    if not isinstance(roles, dict):
        raise HTTPException(400, "falta 'roles'")

    g = roles.get("global")
    if isinstance(g, dict):
        for key in ("provider", "model", "base_url", "api_key", "api_key_env"):
            if key in g and g[key] is not None:
                llm[key] = g[key]
        if not (g.get("provider") or g.get("model") or g.get("base_url")):
            pass  # no limpiar el global sin señal explícita

    for role in ROLES:
        section = roles.get(role)
        if not isinstance(section, dict):
            continue
        if section.get("clear"):
            llm.pop(role, None)
            continue
        override = {
            k: v for k, v in section.items()
            if v not in (None, "") and k in (
                "provider", "model", "base_url", "api_key", "api_key_env",
            )
        }
        if override:
            llm[role] = override
        else:
            llm.pop(role, None)
    data["llm"] = llm
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data), encoding="utf-8")
    return {"ok": True, "llm": {k: v for k, v in llm.items() if k != "api_key"}}


# --------------------------------------------------------------------------
# ejecución (runs + eventos en vivo)
# --------------------------------------------------------------------------

@app.post("/api/run")
def run_start(body: dict[str, Any]):
    goal = str(body.get("goal", "")).strip()
    if not goal:
        raise HTTPException(400, "falta 'goal'")
    root = _root()
    _need_store(root)
    run_id = runner.start_run(
        goal=goal,
        root=str(root),
        resume=body.get("resume"),
        max_rounds=int(body.get("max_rounds", 3)),
    )
    return {"run_id": run_id, "status": "running"}


@app.get("/api/run/{run_id}")
def run_poll(run_id: str):
    info = runner.get_run(run_id)
    if info is None:
        raise HTTPException(404, f"run {run_id} no existe")
    return info


@app.get("/api/run/{run_id}/wait")
def run_wait(run_id: str):
    info = runner.wait_done(run_id, timeout=float(os.getenv("SATELLITE_WEB_WAIT", "120")))
    if info is None:
        raise HTTPException(404, f"run {run_id} no existe")
    return info


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


def _json_dumps(data: dict) -> str:
    import json

    return json.dumps(data, indent=2, ensure_ascii=False)
