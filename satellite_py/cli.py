"""Typer CLI for the Python Satellite runtime."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import typer

from .orchestrator import execute_goal
from .planner import Planner
from .security import SecurityPolicy
from .store import AgentStore

app = typer.Typer(help="Satellite Python runtime")
context_app = typer.Typer(help="Project context commands")
agent_app = typer.Typer(help="Agent management commands")
app.add_typer(context_app, name="context")
app.add_typer(agent_app, name="agent")


def _store() -> AgentStore:
    return AgentStore(Path.cwd())


def _require_project() -> AgentStore:
    store = _store()
    if not store.has_state():
        typer.echo("Error: proyecto no inicializado. Ejecuta: satellite init")
        raise typer.Exit(1)
    return store


# Directorios ignorados al indexar (mismo conjunto que el ContextEngine C++).
_IGNORED_DIRS = {
    ".git", "build", "node_modules", ".satellite", ".agent",
    "out", "dist", ".venv", "venv", "__pycache__", "CMakeFiles",
    ".idea", ".vscode", "build-make", "build2", "build_vs",
}


def _cpp_available() -> bool:
    """True si el binario C++ (backend de delegación) está disponible."""
    from .runtime.cpp_cli_bridge import available

    return available()


def _delegate(args: list[str]) -> None:
    """Ejecuta un subcomando en el binario C++ y propaga salida/exit code."""
    from .runtime.cpp_cli_bridge import run_cpp

    process = run_cpp(args)
    if process.stdout:
        typer.echo(process.stdout.rstrip("\n"))
    if process.stderr:
        typer.echo(process.stderr.rstrip("\n"), err=True)
    if process.returncode != 0:
        raise typer.Exit(process.returncode)


@app.command()
def init() -> None:
    """Initialize .satellite in the current project (delegates to the C++ CLI).

    Uses the C++ backend when available (writes the full config with llm,
    registers the five native agents and writes README.txt). Falls back to the
    Python initializer when the C++ binary is absent.
    """
    if _cpp_available():
        _delegate(["init"])
        return
    store = _store()
    if store.has_state():
        typer.echo("Error: project already initialized (.satellite exists)")
        raise typer.Exit(1)
    store.initialize()
    typer.echo(f"Proyecto inicializado en {store.project_root}")


@app.command()
def agents() -> None:
    """List registered agents (delegates to the C++ CLI for parity)."""
    if _cpp_available():
        _delegate(["agents"])
        return
    store = _store()
    if not store.has_state():
        typer.echo("Error: proyecto no inicializado. Ejecuta: satellite init")
        raise typer.Exit(1)
    registry = store.load_registry()
    typer.echo("ID  NOMBRE  CAPACIDADES  HABILITADO")
    for descriptor in registry.list_agents():
        capabilities = ",".join(descriptor.capabilities)
        typer.echo(
            f"{descriptor.id}  {descriptor.name}  {capabilities}  "
            f"{'si' if descriptor.enabled else 'no'}"
        )


def _delegate_requires_cpp() -> None:
    if not _cpp_available():
        typer.echo("Error: este subcomando requiere el binario C++ (build/satellite)")
        raise typer.Exit(1)


@agent_app.command("info")
def agent_info(agent_id: int) -> None:
    """Show full agent information (delegated to the C++ CLI)."""
    _delegate_requires_cpp()
    _delegate(["agent", "info", str(agent_id)])


@agent_app.command("enable")
def agent_enable(agent_id: int) -> None:
    """Enable an agent (delegated to the C++ CLI)."""
    _delegate_requires_cpp()
    _delegate(["agent", "enable", str(agent_id)])


@agent_app.command("disable")
def agent_disable(agent_id: int) -> None:
    """Disable an agent (delegated to the C++ CLI)."""
    _delegate_requires_cpp()
    _delegate(["agent", "disable", str(agent_id)])


@agent_app.command("create")
def agent_create(spec_path: str) -> None:
    """Compile and register an agent from a spec (delegated to the C++ CLI)."""
    _delegate_requires_cpp()
    _delegate(["agent", "create", spec_path])


@agent_app.command("test")
def agent_test(
    agent_id: int,
    input_file: str | None = typer.Argument(None, help="Archivo JSON con el input (opcional)"),
) -> None:
    """Run an agent once (delegated to the C++ CLI: rebuild + dispatch)."""
    _delegate_requires_cpp()
    args = ["agent", "test", str(agent_id)]
    if input_file:
        args.append(input_file)
    _delegate(args)


@agent_app.command("expand")
def agent_expand(goal: str, capability: str) -> None:
    """Expand a missing capability via the C++ factory (needs DEEPSEEK_API_KEY)."""
    store = _require_project()
    try:
        from .expander import AgentExpander
        from .registry import AgentRegistry

        registry = AgentRegistry()
        for descriptor in store.load_registry().list_agents():
            registry.register_agent(descriptor)
        expander = AgentExpander(registry)
        descriptor = expander.expand(goal, capability)
        store.save_registry(registry)
        typer.echo(f"Agente {descriptor.id} ({descriptor.name}) creado y registrado")
        typer.echo(f"Libreria: {descriptor.library_path}")
    except Exception as error:
        typer.echo(f"Error: {error}")
        raise typer.Exit(1) from error


@context_app.command("build")
def context_build() -> None:
    """Build a project file index with symbols and dependencies (C++/Python)."""
    store = _store()
    if not store.has_state():
        typer.echo("Error: proyecto no inicializado. Ejecuta: satellite init")
        raise typer.Exit(1)
    files = []
    ignored = _IGNORED_DIRS
    for path in store.project_root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        try:
            relative = path.relative_to(store.project_root)
            entry = {
                "path": relative.as_posix(),
                "size": path.stat().st_size,
                "language": _language_of(path),
                "symbols": _index_symbols(path),
                "mtime": int(path.stat().st_mtime),
            }
            try:
                entry["lines"] = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
            except OSError:
                entry["lines"] = 0
            files.append(entry)
        except OSError:
            continue
    # Resolver dependencias entre archivos (includes/imports internos).
    by_path = {entry["path"]: entry for entry in files}
    for entry in files:
        entry["dependencies"] = _index_dependencies(
            store.project_root / entry["path"], by_path, ignored
        )
    output = store.state_root / "context" / "index.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    total_lines = sum(int(entry.get("lines", 0)) for entry in files)
    output.write_text(
        json.dumps(
            {
                "root": str(store.project_root),
                "files": files,
                "total_lines": total_lines,
                "total_files": len(files),
                "build_timestamp": str(int(time.time() * 1000)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    typer.echo(f"Contexto creado: {len(files)} archivos")


@context_app.command("inspect")
def context_inspect() -> None:
    """Inspect the project context index (delegated to the C++ CLI)."""
    _delegate_requires_cpp()
    _delegate(["context", "inspect"])


@context_app.command("get")
def context_get(paths: list[str], max_chars: int = 60000) -> None:
    """Print real file content from the index (delegated to the C++ CLI)."""
    _delegate_requires_cpp()
    _delegate(["context", "get", "--paths"] + paths + ["--max-chars", str(max_chars)])


@app.command()
def run(
    goal: str,
    input: str | None = typer.Option(None, "--input", "-i", help="Respuesta del usuario si el preprocesador pide más contexto"),
    max_rounds: int | None = typer.Option(None, "--rounds", help="Rondas máximas del preprocesador de contexto"),
    resume: str | None = typer.Option(None, "--resume", help="Reanudar desde una sesión previa (ruta a session_*.json o 'last')"),
) -> None:
    """Plan and execute a goal."""
    store = _store()
    if not store.has_state():
        typer.echo("Error: proyecto no inicializado. Ejecuta: satellite init")
        raise typer.Exit(1)
    registry = store.load_registry()
    config_path = store.state_root / "config" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    security = SecurityPolicy()
    security.load_defaults()
    security_config = config.get("security", {}).get("allow", {})
    if security_config:
        security.from_config(security_config)
    catalog = json.dumps(
        [
            {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "capabilities": agent.capabilities,
                "input_schema": agent.input_schema,
                "output_schema": agent.output_schema,
            }
            for agent in registry.list_agents()
            if agent.enabled
        ]
    )
    try:
        # Un solo proveedor (config.json / env) para preprocesar, comprimir y planificar.
        from .context import ContextPreprocessor
        from .llm import load_llm_config
        from .semantic import ContextCompressor

        llm_config = load_llm_config(config_path)
        client = llm_config.create_client()
        preprocessor = ContextPreprocessor(
            client=client,
            project_root=store.project_root,
            max_rounds=max_rounds if max_rounds is not None else int(
                config.get("local_llm", {}).get("max_rounds", 3)
            ),
        )
        preprocessed = preprocessor.preprocess(goal, user_input=input)
        if preprocessed.needs_user_input and not input:
            typer.echo(preprocessed.user_prompt, err=True)
            raise typer.Exit(2)
        # Con --input, el refined_prompt ya incorpora la info del usuario.
        refined_goal = preprocessed.refined_prompt
        # Compresión semántica: el mismo modelo reduce el contexto crudo a un
        # documento JSON neutro (intention/constraints/references) propiedad de
        # Satellite — agnóstico al provider y reutilizable entre corridas.
        raw_context = "\n".join(
            f"=== {path} ===\n{content}"
            for path, content in _extract_file_context(preprocessed.refined_prompt).items()
        )
        compressor = ContextCompressor(client)
        neutral = compressor.compress(goal, raw_context)
        session_dir = store.state_root / "context"
        # --resume: continuar desde el historial comprimido neutro de una sesión
        # previa (sin re-preprocesar/ re-comprimir el contexto acumulado).
        resume_path: str | None = None
        if resume:
            from .context_schema import last_session_path

            if resume == "last":
                resume_path = last_session_path(str(session_dir))
            else:
                candidate = Path(resume)
                resume_path = str(candidate if candidate.is_absolute() else Path.cwd() / candidate)
            if not resume_path or not Path(resume_path).is_file():
                typer.echo(f"Error: sesión a reanudar no encontrada: {resume}")
                raise typer.Exit(1)
        result = execute_goal(
            registry,
            security,
            refined_goal,
            catalog,
            Planner(client, llm_config.provider),
            context=None if resume_path else neutral,
            project_root=str(store.project_root),
            session_dir=str(session_dir),
            max_rounds=preprocessor.max_rounds,
            resume_session=resume_path,
        )
    except Exception as error:
        typer.echo(f"Error: {error}")
        raise typer.Exit(1) from error
    typer.echo(result.get("summary", ""))
    if not result.get("ok", False):
        raise typer.Exit(1)


@app.command()
def serve_local() -> None:
    """Serve the bundled local llama.cpp model on local_llm.port.

    Launches ``llama-server`` from ``third_party/llama-*/`` with the GGUF from
    ``local_llm.model`` and blocks until interrupted (Ctrl+C stops the server).
    The preprocessor/planner then use ``llm.provider = "local"`` against
    ``http://localhost:<port>``.
    """
    import subprocess
    import time
    import urllib.request

    store = _require_project()
    config_path = store.state_root / "config" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    local_cfg = config.get("local_llm", {}) if isinstance(config.get("local_llm"), dict) else {}
    port = int(local_cfg.get("port", 8080))
    framework_root = Path(__file__).resolve().parents[1]

    # Localizar llama-server (bundled en third_party/ o ruta explícita del config).
    server_exe = None
    explicit = local_cfg.get("path")
    if explicit and Path(explicit).is_file():
        server_exe = str(explicit)
    else:
        matches = sorted((framework_root / "third_party").glob("llama-*/llama-server*"))
        server_exe = next((str(p) for p in matches if p.is_file()), None)
    if not server_exe:
        typer.echo("Error: no se encontró llama-server en third_party (ni local_llm.path)")
        raise typer.Exit(1)

    # Modelo GGUF: local_llm.model (ruta absoluta o relativa al framework).
    model_value = local_cfg.get("model")
    if not model_value:
        matches = sorted((framework_root / "third_party").glob("llama-*/*.gguf"))
        model_value = str(matches[0]) if matches else ""
    model_path = Path(model_value)
    if not model_path.is_absolute():
        model_path = framework_root / model_path
    if not model_path.is_file():
        typer.echo(f"Error: no se encontró el GGUF: {model_value}")
        raise typer.Exit(1)

    context_size = int(local_cfg.get("context_size", 8192))
    typer.echo(f"Lanzando llama-server en http://localhost:{port} con {model_path.name} ...")
    process = subprocess.Popen(
        [server_exe, "-m", str(model_path), "--port", str(port), "-c", str(context_size)],
    )
    try:
        url = f"http://localhost:{port}/health"
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        ready = True
                        break
            except Exception:  # noqa: BLE001 - reintentar mientras arranca
                time.sleep(1)
        if ready:
            typer.echo("llama-server listo. Ctrl+C para detener.")
        else:
            typer.echo("Error: llama-server no respondió en 60s", err=True)
        process.wait()
    except KeyboardInterrupt:
        typer.echo("\nDeteniendo llama-server ...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise typer.Exit(0)


@app.command()
def doctor() -> None:
    """Check project state and required runtime files."""
    store = _store()
    checks = {
        "project_initialized": store.has_state(),
        "config": (store.state_root / "config" / "config.json").is_file(),
        "registry": (store.state_root / "registry" / "agents.json").is_file(),
        "agent_host": Path(os.getenv("SATELLITE_AGENT_HOST", "./build/satellite_agent_host")).is_file(),
        "cpp_cli": _cpp_available(),
    }
    for name, passed in checks.items():
        typer.echo(f"[{ 'OK' if passed else 'FAIL'}] {name}")
    if not all(checks.values()):
        raise typer.Exit(1)
    # Delegar los chequeos del C++ (g++, config parseable, registry>0, agente sum).
    _delegate(["doctor"])
    typer.echo("doctor: todo correcto")


@app.command()
def version() -> None:
    """Show the Satellite version."""
    typer.echo("satellite 1.0.0")


def _language_of(path: Path) -> str:
    """Classify a source file by extension for the context index."""
    suffix = path.suffix.lower()
    if suffix in {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".hxx", ".c"}:
        return "C++"
    if suffix == ".py":
        return "Python"
    if suffix == ".json":
        return "JSON"
    return ""


def _index_dependencies(
    path: Path,
    by_path: dict[str, dict],
    ignored: set[str],
) -> list[str]:
    """Extract internal/external dependencies of one source file.

    Mirrors the C++ ``ContextEngine::extract_deps_cpp/python``: C++ ``#include
    "local"`` / ``<system>`` and Python ``import x`` / ``from x import``. A
    dependency is resolved to the project path when the target exists in the
    index; otherwise it is recorded as ``external:<name>`` (like the C++
    ``DependencyInfo.external`` flag).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = path.suffix.lower()
    deps: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        target = ""
        if suffix in {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".hxx", ".c"}:
            match = re.match(r'#include\s*"([^"]+)"', line)
            if not match:
                match = re.match(r"#include\s*<([^>]+)>", line)
            if match:
                target = match.group(1)
        elif suffix == ".py":
            match = re.match(r"^(?:import|from)\s+([\w.]+)", line)
            if match:
                target = match.group(1).split(".")[0]
        if not target:
            continue
        resolved = _resolve_dependency(target, path, by_path)
        deps.append(resolved)
    # Dedupe preservando orden.
    seen: set[str] = set()
    return [dep for dep in deps if not (dep in seen or seen.add(dep))]


def _resolve_dependency(
    target: str,
    source_path: Path,
    by_path: dict[str, dict],
) -> str:
    """Resolve ``target`` (as written) to an indexed project path."""
    source_dir = source_path.parent
    candidates = [
        source_dir / target,
        source_dir / (target + ".h"),
        source_dir / (target + ".hpp"),
        source_dir / (target + ".py"),
    ]
    # El directorio raíz del proyecto: subimos hasta que el path relativo
    # resultante exista en el índice.
    current = source_dir
    while current != current.parent:
        for candidate in candidates:
            try:
                relative = candidate.relative_to(current).as_posix()
            except ValueError:
                continue
            if relative in by_path:
                return relative
        current = current.parent
    # No está en el índice → externa (igual que el flag external del C++).
    return f"external:{target}"


def _extract_file_context(refined_prompt: str) -> dict[str, Any]:
    """Extract ``{path: content}`` blocks from a preprocessed prompt.

    The preprocessor embeds real file contents as ``=== path ===`` blocks;
    this turns them into a structured dict that is passed to every agent
    request as ``request["context"]``.
    """
    blocks: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in refined_prompt.splitlines():
        match = re.match(r"^=== (.+) ===$", line.strip())
        if match:
            if current is not None:
                blocks[current] = "\n".join(lines)
            current = match.group(1)
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        blocks[current] = "\n".join(lines)
    return blocks


def _index_symbols(path: Path) -> list[str]:
    """Extract top-level symbol names (C++ and Python) for the context index.

    Uses lightweight line-based regexes (like the C++ ContextEngine). The
    index is used by the preprocessor to resolve ``symbols_needed``.
    """
    symbols: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return symbols
    suffix = path.suffix.lower()
    if suffix in {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".hxx", ".c"}:
        # Funciones y clases/métodos a nivel de columna 0 o tras ``public:``.
        for match in re.finditer(r"^\s*(?:virtual\s+|static\s+|inline\s+|explicit\s+)*"
                                 r"(?:[\w:<>,\s&*]+?)\s+(\w+)\s*\(", text, re.MULTILINE):
            name = match.group(1)
            if name not in {"if", "for", "while", "switch", "catch", "return", "sizeof", "decltype"}:
                symbols.append(name)
        for match in re.finditer(r"^\s*(?:class|struct)\s+(\w+)", text, re.MULTILINE):
            symbols.append(match.group(1))
    elif suffix == ".py":
        for match in re.finditer(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", text, re.MULTILINE):
            symbols.append(match.group(1))
        for match in re.finditer(r"^\s*class\s+(\w+)", text, re.MULTILINE):
            symbols.append(match.group(1))
    # Dedupe preservando orden.
    seen: set[str] = set()
    return [symbol for symbol in symbols if not (symbol in seen or seen.add(symbol))]


if __name__ == "__main__":
    app()
