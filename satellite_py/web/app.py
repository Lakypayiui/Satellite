"""FastAPI backend for the Satellite web UI.

Static SPA served from ``web/static``; JSON/streaming API in ``/api``.
The runtime bridge lives in ``web/runner`` (events, graph, runs).

Run with::

    python -m satellite_py.web            # localhost:7900 (uvicorn)
"""

from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
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

# Área permitida: SATELLITE_WEB_ROOT o el cwd. Todo cambio de raíz y todo
# acceso a archivos/dirs queda confinado a este árbol (defensa en profundidad
# para una consola que además puede escribir archivos).
_MAX_GOAL_CHARS = 4000
_RUN_WINDOW_SECS = 60.0
_run_times: list[float] = []
_run_lock = threading.Lock()


def _run_limit() -> int:
    try:
        return max(1, int(os.getenv("SATELLITE_WEB_MAX_RUNS", "5")))
    except ValueError:
        return 5


def _auth_token() -> str | None:
    token = os.getenv("SATELLITE_WEB_TOKEN", "").strip()
    return token or None


@app.middleware("http")
async def _auth_middleware(request, call_next):
    """Si SATELLITE_WEB_TOKEN está definida, toda ruta /api exige el token.

    Se compara en tiempo constante (secrets.compare_digest). El token llega
    por header ``X-Satellite-Token`` o cookie ``satellite_token``; sin token
    configurado la consola sigue abierta (bind local por defecto).
    """
    token = _auth_token()
    if token and request.url.path.startswith("/api"):
        supplied = (
            request.headers.get("x-satellite-token")
            or request.cookies.get("satellite_token")
            or ""
        )
        if not secrets.compare_digest(supplied, token):
            return JSONResponse({"detail": "no autorizado"}, status_code=401)
    return await call_next(request)


def _base_root() -> Path:
    return Path(os.getenv("SATELLITE_WEB_ROOT") or os.getcwd()).resolve()


def _within_base(candidate: Path) -> bool:
    try:
        candidate.relative_to(_base_root())
        return True
    except ValueError:
        return False


def _root() -> Path:
    if _ROOT and _within_base(Path(_ROOT).resolve()):
        return project_root(_ROOT)
    return project_root(_base_root())


def _is_satellite_meta(root: Path, full: Path) -> bool:
    """Rechaza lecturas/escrituras dentro de ``.satellite/`` (config, keys)."""
    try:
        full.relative_to(root / ".satellite")
        return True
    except ValueError:
        return False


def _sanitize_goal(goal: str) -> str:
    """Quita caracteres de control y limita la longitud (entrada al LLM)."""
    cleaned = "".join(
        ch for ch in goal if ch in "\n\t" or ch >= " "
    )
    return cleaned[:_MAX_GOAL_CHARS]


def _throttle_run() -> None:
    """Rate-limit simple de runs (DoS por runs concurrentes)."""
    limit = _run_limit()
    now = time.monotonic()
    with _run_lock:
        _run_times[:] = [t for t in _run_times if now - t < _RUN_WINDOW_SECS]
        if len(_run_times) >= limit:
            raise HTTPException(429, "demasiados runs en este minuto")
        _run_times.append(now)


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
    if path:
        base = Path(path).expanduser().resolve()
        if not _within_base(base):
            raise HTTPException(403, "fuera del área permitida")
    else:
        base = _base_root()
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
    """Cambia la raíz de proyecto activa (abrir carpeta desde la UI).

    La nueva raíz DEBE estar dentro del área permitida
    (``SATELLITE_WEB_ROOT`` o el cwd del servidor): apuntar el root a
    ``C:\\`` ya no es posible (el límite dejaría de proteger).
    """
    global _ROOT
    candidate = Path(str(body.get("path", "") or "")).expanduser()
    if not candidate.is_dir():
        raise HTTPException(400, f"no existe: {candidate}")
    resolved = candidate.resolve()
    if not _within_base(resolved):
        raise HTTPException(403, "fuera del área permitida (SATELLITE_WEB_ROOT o cwd)")
    _ROOT = str(resolved)
    return {"ok": True, "root": _ROOT}


@app.post("/api/project/init")
def project_init():
    """Inicializa el proyecto activo (`.satellite/` + registry + config).

    Delega al binario C++ cuando está disponible (registra los agentes
    nativos 1-5 y escribe el config completo); sin binario, usa el
    inicializador Python (registry vacío).
    """
    root = _root()
    store = runner._store(root)
    if store.has_state():
        return {"ok": False, "error": "proyecto ya inicializado (.satellite existe)"}

    # Mismo comportamiento que la CLI Python unificada: delegar al C++.
    try:
        from ..runtime.cpp_cli_bridge import available as cpp_available
        from ..runtime.cpp_cli_bridge import cpp_bin, run_cpp

        if cpp_available():
            # run_cpp corre con cwd del proceso; lanzamos con cwd=root para
            # que el init C++ escriba en el proyecto activo.
            process = subprocess.run(
                [cpp_bin(), "init"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(root),
            )
            if process.returncode != 0:
                return {"ok": False, "error": process.stderr.strip() or "init C++ falló"}
            return {"ok": True, "root": str(root), "initialized": True, "via": "cpp"}
    except Exception:  # noqa: BLE001 - fallback al inicializador Python
        pass

    store.initialize()
    return {"ok": True, "root": str(root), "initialized": True, "via": "python"}


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
    if _is_satellite_meta(root, base):
        raise HTTPException(403, "área restringida (.satellite)")
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
    if _is_satellite_meta(root, full):
        raise HTTPException(403, "área restringida (.satellite)")
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
    if _is_satellite_meta(root, full):
        raise HTTPException(403, "área restringida (.satellite)")
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return {"ok": True, "path": path}


@app.post("/api/file/ask")
def file_ask(body: dict[str, Any]):
    """Responde preguntas sobre un archivo del proyecto con el LLM configurado.

    El usuario NO da la ruta (el frontend ya tiene el archivo seleccionado);
    ``path`` identifica el archivo y ``question`` es la pregunta. El contenido
    del archivo se inyecta como contexto (recortado) y el LLM responde sin
    ejecutar agentes.
    """
    path = str(body.get("path", ""))
    question = str(body.get("question", "")).strip()
    if not path or not question:
        raise HTTPException(400, "faltan 'path' y 'question'")

    root = _root()
    full = (root / path).resolve()
    if full != root and root not in full.parents:
        raise HTTPException(403, "fuera del proyecto")
    if _is_satellite_meta(root, full):
        raise HTTPException(403, "área restringida (.satellite)")
    if not full.is_file():
        raise HTTPException(404, "archivo no existe")
    try:
        content = full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise HTTPException(400, "no es un archivo de texto")

    # Proveedor LLM configurado (rol orquestador) para responder.
    from ..llm import llm_role_config

    cfg = llm_role_config(_config(root) or {}, "orchestrator")
    client = cfg.create_client()
    prompt = (
        f"Archivo: {path}\n\n"
        "--- contenido ---\n"
        f"{content[:16000]}\n"
        "--- fin ---\n\n"
        f"Pregunta sobre este archivo: {question}\n"
        "Responde de forma clara y concisa, en español, citando el código si es útil."
    )
    try:
        answer = client.complete("", prompt, max_tokens=1500)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(502, f"el proveedor LLM falló: {error}")
    return {"path": path, "question": question, "answer": answer}


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
    goal = _sanitize_goal(str(body.get("goal", "")).strip())
    if not goal:
        raise HTTPException(400, "falta 'goal'")
    _throttle_run()
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
