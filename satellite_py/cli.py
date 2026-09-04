"""Typer CLI for the Python Satellite runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from .orchestrator import execute_goal
from .planner import Planner
from .security import SecurityPolicy
from .store import AgentStore

app = typer.Typer(help="Satellite Python runtime")
context_app = typer.Typer(help="Project context commands")
app.add_typer(context_app, name="context")


def _store() -> AgentStore:
    return AgentStore(Path.cwd())


@app.command()
def init() -> None:
    """Initialize .satellite in the current project."""
    store = _store()
    if store.has_state():
        typer.echo("Error: project already initialized (.satellite exists)")
        raise typer.Exit(1)
    store.initialize()
    typer.echo(f"Proyecto inicializado en {store.project_root}")


@app.command()
def agents() -> None:
    """List registered agents."""
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


@context_app.command("build")
def context_build() -> None:
    """Build a lightweight project file index."""
    store = _store()
    if not store.has_state():
        typer.echo("Error: proyecto no inicializado. Ejecuta: satellite init")
        raise typer.Exit(1)
    files = []
    ignored = {".git", ".satellite", "build", "build-make", "build2", "build_vs"}
    for path in store.project_root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        try:
            relative = path.relative_to(store.project_root)
            files.append({"path": relative.as_posix(), "size": path.stat().st_size})
        except OSError:
            continue
    output = store.state_root / "context" / "index.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"root": str(store.project_root), "files": files}, indent=2), encoding="utf-8")
    typer.echo(f"Contexto creado: {len(files)} archivos")


@app.command()
def run(goal: str) -> None:
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
        result = execute_goal(registry, security, goal, catalog, Planner.from_environment())
    except Exception as error:
        typer.echo(f"Error: {error}")
        raise typer.Exit(1) from error
    typer.echo(result.get("summary", ""))
    if not result.get("ok", False):
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Check project state and required runtime files."""
    store = _store()
    checks = {
        "project_initialized": store.has_state(),
        "config": (store.state_root / "config" / "config.json").is_file(),
        "registry": (store.state_root / "registry" / "agents.json").is_file(),
        "agent_host": Path(os.getenv("SATELLITE_AGENT_HOST", "./build/satellite_agent_host")).is_file(),
    }
    for name, passed in checks.items():
        typer.echo(f"[{ 'OK' if passed else 'FAIL'}] {name}")
    if not all(checks.values()):
        raise typer.Exit(1)
    typer.echo("doctor: todo correcto")


if __name__ == "__main__":
    app()
