"""Satellite CLI (Typer facade over the runtime)."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from satellite.cli.console import say
from satellite.cli.provider_cli import provider_app
from satellite.cli.runtime_builder import build_runtime
from satellite.config import providers as provider_config
from satellite.persistence.store import FileStore

app = typer.Typer(help="Satellite 2.0 — microagent framework CLI.")
skills_app = typer.Typer(help="Gestión de skills globales.")
tools_app = typer.Typer(help="Herramientas (primitivas) del runtime.")
app.add_typer(skills_app, name="skills")
app.add_typer(tools_app, name="tools")
app.add_typer(provider_app, name="provider")


@app.callback()
def main(
    safe_mode: bool = typer.Option(
        False, "--safe-mode", help="Arranca sólo con los 3 agentes base y primitivas."
    ),
) -> None:
    """Configuración global de la CLI."""
    from satellite.cli import runtime_builder

    runtime_builder.set_safe_mode(safe_mode)


# ---------------------------------------------------------------- ejecución


@app.command()
def run(
    goal: str = typer.Argument(..., help="The user goal to execute."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
    yes: bool = typer.Option(False, "--yes", help="Auto-aprobar propuestas (sin preguntar)."),
) -> None:
    """Run the orchestrator for a goal and print the summary."""
    orchestrator, _bus, _store, _ = build_runtime(project)
    from satellite.approval.base import AutoApprovalPort
    from satellite.approval.cli import CliApproval
    from satellite.core.task import Task

    import asyncio

    services = getattr(orchestrator, "services", None)
    if services is not None and getattr(services, "approval", None) is None:
        services.approval = AutoApprovalPort() if yes else CliApproval()
    result = asyncio.run(orchestrator.execute(Task(goal=goal, project_root=project.resolve())))
    _print_result(result, project=project.resolve())


@app.command()
def plan(
    goal: str = typer.Argument(..., help="Tarea ambigua o a planificar."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Modo plan: propone alternativas (desarrolladas por el LLM) para elegir."""
    import asyncio

    from satellite.orchestration.chat import ChatSession

    orchestrator, _bus, _store, _registry = build_runtime(project)
    chat = ChatSession(project.resolve(), orchestrator)
    reply = asyncio.run(chat.send(goal, plan_mode=True))
    say(reply.text)
    payload = (reply.output or {}).get("plan_options")
    if not payload:
        return
    recommendation = payload.get("recommendation", "")
    for index, option in enumerate(payload.get("options", []), 1):
        mark = "  ★ recomendada" if option.get("id") == recommendation else ""
        say(f"  {index}. {option.get('title')}{mark}")
        say(f"     {option.get('summary')}")
        for step in option.get("approach", []):
            say(f"       · {step}")
    say("  o describe tu propio enfoque en un mensaje nuevo.")


@app.command()
def tui(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Lanza la interfaz interactiva (TUI)."""
    from satellite.tui.app import run_tui

    run_tui(project)


@app.command()
def agents(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
    create: str = typer.Option("", "--create", help="Necesidad de capacidad a crear."),
    yes: bool = typer.Option(False, "--yes", help="Auto-aprobar la creación."),
) -> None:
    """List the agent catalog, or create a new agent from a capability need."""
    import asyncio

    from satellite.approval.base import AutoApprovalPort
    from satellite.approval.cli import CliApproval

    orchestrator, _bus, _store, registry = build_runtime(project)
    if create:
        expander = getattr(orchestrator, "expander", None)
        if expander is None:
            say("el runtime no tiene expander (no se pueden crear agentes)")
            raise typer.Exit(code=1)
        port = AutoApprovalPort() if yes else CliApproval()
        outcome = asyncio.run(expander.expand(create, approval_port=port))
        detail = (
            outcome.spec.id
            if outcome.spec is not None
            else outcome.reason or outcome.question or outcome.reuse_candidate or ""
        )
        say(f"{outcome.kind}: {detail}")
        return
    for spec in registry.all():
        say(f"{spec.id}: {spec.name} | caps={', '.join(spec.capabilities)} | tools={', '.join(spec.tools)}")


@app.command()
def tasks(project: Path = typer.Option(Path.cwd(), "--project", help="Project root.")) -> None:
    """List persisted tasks."""
    store = FileStore(project.resolve())
    for task_id in store.list_tasks():
        say(task_id)


@app.command()
def status(
    task_id: str = typer.Argument(..., help="Task id."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Show status from stored events."""
    import asyncio

    store = FileStore(project.resolve())
    events = asyncio.run(store.load_events(task_id))
    states = [e.event_type.value for e in events]
    say(f"task={task_id} eventos={len(events)}")
    for st in states:
        say(f"  - {st}")


@app.command()
def inspect(
    task_id: str = typer.Argument(..., help="Task id."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Show the serialized graph for a task."""
    import asyncio
    import json

    store = FileStore(project.resolve())
    graph = asyncio.run(store.load_graph(task_id))
    say(json.dumps(graph.to_dict(), ensure_ascii=False, indent=2))


@app.command()
def config(
    show: bool = typer.Option(False, "--show", help="Show config."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Show .satellite/config/config.json."""
    cfg = project.resolve() / ".satellite" / "config" / "config.json"
    if not cfg.exists():
        say("(sin config: se usa FakeLLM)")
        return
    say(cfg.read_text(encoding="utf-8"))


@app.command()
def init(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Inicializa el proyecto (crea .satellite/ + config por defecto)."""
    import json

    root = project.resolve()
    cfg_dir = root / ".satellite" / "config"
    cfg_path = cfg_dir / "config.json"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (root / ".satellite" / "observability").mkdir(parents=True, exist_ok=True)
    # La migración se aplica SIEMPRE (idempotente): re-ejecutar `init` también
    # repara un directorio de datos que se quedó en una versión de esquema vieja.
    FileStore(root).migrate()
    if cfg_path.exists():
        say(f"proyecto ya inicializado ({cfg_path})")
        return
    cfg_path.write_text(
        json.dumps({"name": "openai", "model": "gpt-4o-mini", "base_url": "", "api_key": ""}, indent=2),
        encoding="utf-8",
    )
    say(f"inicializado: {root / '.satellite'}")


# ---------------------------------------------------------------- skills


def _load_skills():
    """Registro de skills globales (sin necesitar proveedor ni runtime)."""
    from satellite.kernel.paths import ensure_dirs, resolve_paths
    from satellite.skills.registry import SkillRegistry

    paths = ensure_dirs(resolve_paths())
    registry = SkillRegistry(paths.skills_root)
    registry.load_all()
    return registry, paths


@tools_app.command("list")
def tools_list() -> None:
    """Lista las herramientas del runtime (todas del kernel, con id reservado)."""
    from satellite.cli.runtime_builder import build_tool_registry

    items = build_tool_registry().all()
    for tool in items:
        categories = ", ".join(getattr(tool, "categories", ()) or ()) or "-"
        say(f"  {tool.id:<14} {categories:<16} {tool.description}")
    say("")
    say(f"{len(items)} herramientas del kernel, todas con id reservado.")
    say(
        "Ninguna se puede suplantar con un agente o skill, y no salen en "
        "'skills list': son kernel inmutable (no se editan ni se borran)."
    )


@skills_app.command("list")
def skills_list() -> None:
    """Lista las skills globales con su estado y uso."""
    from satellite.skills.cache import SkillResultCache

    registry, paths = _load_skills()
    skills = registry.all()
    cache = SkillResultCache()
    if not skills:
        say("(todavía no hay skills creadas)")
        say(
            "  Ojo: las primitivas del runtime (run_python, run_shell, read_file, "
            "write_file, list_dir, search_files)"
        )
        say("  NO son skills, por eso no aparecen aquí. Míralas con 'satellite tools list'.")
        say("  Para crear una skill: pídesela al agente ('/create ...') o al curador ('/curate').")
        return
    for manifest in skills:
        say(
            f"{manifest.id}: {manifest.description} [{manifest.status.value}] "
            f"uses={manifest.stats.uses}"
        )
        if getattr(manifest, "deterministic", False):
            reason = cache.reason(manifest, skill_dir=paths.skills_root / manifest.id)
            say(
                "    memoizable: determinista (auditado)"
                if not reason
                else f"    NO memoizable: {reason}"
            )
    say("")
    say("(las primitivas del runtime no aparecen aquí: 'satellite tools list')")


@skills_app.command("search")
def skills_search(query: str = typer.Argument(..., help="Descripción a buscar.")) -> None:
    """Busca skills por descripción (BM25, sin LLM)."""
    registry, _paths = _load_skills()
    hits = registry.find_similar(query)
    if not hits:
        say("(sin coincidencias)")
        return
    for hit in hits:
        say(f"{hit.id} ({hit.score:.3f}) {hit.fields.get('description', '')}")


@skills_app.command("edit")
def skills_edit(
    skill_id: str = typer.Argument(...),
    description: str = typer.Option("", "--description", help="Nueva descripción."),
    tags: str = typer.Option("", "--tags", help="Tags separados por coma."),
) -> None:
    """Edita la descripción o los tags de una skill (marca nueva aprobación)."""
    from satellite.skills.models import SkillManifest

    registry, _paths = _load_skills()
    data = registry.get(skill_id).to_dict()
    if description:
        data["description"] = description
    if tags:
        data["tags"] = [part.strip() for part in tags.split(",") if part.strip()]
    registry.replace_manifest(SkillManifest.from_dict(data))
    say(f"editada: {skill_id}")


@skills_app.command("delete")
def skills_delete(skill_id: str = typer.Argument(...)) -> None:
    """Elimina una skill global."""
    registry, _paths = _load_skills()
    registry.remove(skill_id)
    say(f"borrada: {skill_id}")


@app.command()
def stats(project: Path = typer.Option(Path.cwd(), "--project", help="Project root.")) -> None:
    """Muestra métricas de skills y de cache (la línea base es una estimación)."""
    from satellite.metrics.cache_report import load_cache_report
    from satellite.metrics.stats import build_report

    registry, _paths = _load_skills()
    cache = load_cache_report(project)
    # [AJ-8] el coste por uso es el REAL (con cache): input efectivo + salida
    report = build_report(
        registry.all(),
        call_cost_tokens=int(cache.real_tokens_per_call or 0),
        cache=cache.to_dict(),
    )
    say(
        f"skills={len(report.skills)} ahorro_total={report.total_savings} "
        "(línea base = estimación)"
    )
    for metric in report.skills:
        say(
            f"  {metric.skill_id}: usos={metric.uses} ok={metric.successes} "
            f"fail={metric.failures} ahorro={metric.savings} "
            f"equilibrio={metric.break_even_uses}"
        )
    say("")
    say(cache.format())
    from satellite.context.compaction import format_usage, project_usage

    say(format_usage(project_usage(project)))


@app.command()
def compact(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
    keep: int = typer.Option(0, "--keep", help="Turnos a conservar (0 = config)."),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Resumen determinista, sin llamar al modelo."
    ),
) -> None:
    """Compacta la conversación guardada (resumen + últimos turnos)."""
    import asyncio

    from satellite.context.compaction import format_usage, project_usage
    from satellite.orchestration.chat import ChatSession, conversation_path

    root = project.resolve()
    path = conversation_path(root)
    if not path.exists():
        say(f"no hay conversación guardada ({path})")
        say("  se guarda sola al chatear en la TUI ('satellite tui')")
        raise typer.Exit(code=1)

    orchestrator = None
    if not no_llm:
        try:
            orchestrator, _bus, _store, _registry = build_runtime(root)
        except Exception as exc:  # noqa: BLE001
            say(f"! sin proveedor utilizable ({exc}); resumen determinista")

    session = ChatSession(root, orchestrator)
    turns = session.load()
    say(f"conversación: {turns} turnos")
    say(format_usage(project_usage(root, turns=session.turns())))
    result = asyncio.run(session.compact_now(keep=keep or None))
    if not result.dropped:
        reason = (
            "no reduciría el contexto"
            if result.method == "sin-efecto"
            else "no hacía falta (conversación corta)"
        )
        say(f"no se compactó: {reason}")
        return
    say(f"compactado: {result.dropped} turnos → resumen ({result.method})")
    say(format_usage(project_usage(root, turns=session.turns())))
    say(f"guardado en {path}")


# ---------------------------------------------------------------- mantenimiento


@app.command()
def setup() -> None:
    """Detecta Docker (N1) y guarda la decisión (una sola vez)."""
    from satellite.kernel.paths import ensure_dirs, resolve_paths
    from satellite.sandbox.setup import detect_docker, record_decision

    paths = ensure_dirs(resolve_paths())
    status = detect_docker()
    if status.available:
        say(f"docker disponible: {status.version} (sandbox N1 listo)")
        record_decision(paths.global_root, enabled=True)
    else:
        say(status.instructions)
        record_decision(paths.global_root, enabled=False)


@app.command()
def doctor(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Diagnostica entorno, config, proveedor, catálogo, esquema y sandbox."""
    import asyncio  # noqa: F401
    import os

    from satellite.kernel import migrations
    from satellite.kernel.manifest import is_reserved, verify_and_restore
    from satellite.kernel.paths import ensure_dirs, resolve_paths

    root = project.resolve()
    cfg_path = root / ".satellite" / "config" / "config.json"
    ok = True
    if not cfg_path.exists():
        say("✗ config: falta .satellite/config/config.json (usa 'satellite init')")
        ok = False
    else:
        say("✓ config: presente")
    try:
        orchid, _bus, _store, registry = build_runtime(root)
    except Exception as exc:  # noqa: BLE001
        say(f"✗ runtime: {exc}")
        ok = False
        registry = None
    if registry is not None:
        specs = registry.all()
        say(f"✓ catálogo: {len(specs)} agentes")
        for spec in specs:
            say(f"    {spec.id}: {spec.capabilities}")
        tool_registry = getattr(getattr(orchid, "services", None), "tools", None)
        if tool_registry is not None:
            found = tool_registry.all()
            primitives = sum(1 for tool in found if is_reserved(tool.id))
            say(f"✓ herramientas: {len(found)} ({primitives} primitivas del kernel)")
        if getattr(registry, "errors", None):
            say(f"✗ catálogo: {len(registry.errors)} specs inválidos")
            ok = False

    paths = ensure_dirs(resolve_paths(root))
    version = migrations.current_version(paths.project_data)
    if version >= migrations.SCHEMA_VERSION:
        say(f"✓ esquema: v{version}")
    else:
        say(f"✗ esquema: v{version} (usa 'satellite init' o migrate)")
        ok = False

    # Proveedor activo (el que usará el runtime) y estado de la clave.
    active = provider_config.active_provider(root)
    if active is None:
        say("✗ proveedor: sin proveedor activo (configúralo con 'satellite provider')")
        ok = False
    else:
        key = "clave ok" if active.api_key_set else "SIN clave"
        say(f"✓ proveedor: {active.name} · {active.model}  ({key})")
        if active.base_url:
            say(f"    base_url: {active.base_url}")
        if active.api_key_env:
            say(f"    api_key_env: {active.api_key_env}")
        if not active.api_key_set and not active.base_url.startswith("http://localhost"):
            say("    ! puede faltar la API key: 'satellite provider --project .' para configurarla")
        saved = provider_config.list_providers(root)
        say(f"✓ proveedores guardados: {len(saved)}")
        for provider in saved:
            say(f"    {provider.id}: {provider.label()} ({'clave ok' if provider.api_key_set else 'sin clave'})")
        roles = provider_config.role_config(root)
        if roles:
            for role, section in roles.items():
                say(f"    rol {role} -> {section.get('provider', '')} ({section.get('model', '')})")

    quarantines = list(paths.agents_root.glob("_quarantine/*")) + list(
        paths.skills_root.glob("_quarantine/*")
    )
    say(f"✓ cuarentenas: {len(quarantines)}")

    # Aislamiento real, sin promesas de más: N0 aísla el proceso, no el sistema
    # de archivos, y los modos auto/unrestricted aprueban sin preguntar.
    from satellite.kernel.policy import PolicyMode, load_settings

    settings = load_settings(cfg_path)
    level = str(settings.sandbox_level or "N0").upper()
    mode = settings.policy_mode.value
    permissive = settings.policy_mode is PolicyMode.UNRESTRICTED or level == "N2"
    if permissive:
        say(f"! sandbox: {level} · política {mode} — SIN confinamiento")
        say("    ! las skills y los subprocesos corren con tus permisos y sin límites")
    else:
        say(f"✓ sandbox: {level} · política {mode}")
    if level == "N0":
        say("    i N0 es aislamiento de PROCESO (entorno limpio, sin red, con timeout),")
        say("      no del sistema de archivos: para confinarlo de verdad usa N1")
    if settings.policy_mode is PolicyMode.AUTO:
        say("    i política auto: las acciones que exigen aprobación se aprueban solas")

    env_keys = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY") if os.getenv(k)]
    say("✓ env keys: " + (", ".join(env_keys) if env_keys else "(ninguna)"))
    say("DOCTOR: " + ("PASS" if ok else "FALLOS ENCONTRADOS"))


@app.command()
def reset(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
    keep_skills: bool = typer.Option(True, "--keep-skills/--factory", help="Conserva las skills (por defecto)."),
) -> None:
    """Restaura el estado base: limpia los agentes creados (y las skills si --factory)."""
    from satellite.kernel.paths import ensure_dirs, resolve_paths

    paths = ensure_dirs(resolve_paths(project))
    _clear_dir(paths.agents_root)
    if not keep_skills:
        _clear_dir(paths.skills_root)
    say(
        "reset: agentes limpiados" + ("" if keep_skills else " y skills eliminadas")
    )


@app.command()
def restore(
    name: str = typer.Argument(..., help="Nombre del snapshot."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Restaura el directorio de datos desde un snapshot."""
    store = FileStore(project.resolve())
    store.restore(name)
    say(f"restaurado: {name}")


def _clear_dir(root: Path) -> None:
    """Borra el contenido de un directorio de datos (sin borrar el directorio)."""
    if not root.exists():
        return
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink()


def _print_result(result, *, project: Path | None = None) -> None:
    say(f"status: {result.status.value}")
    say(f"summary: {result.summary}")
    if result.error:
        say(f"error: {result.error}")
    if project is not None:
        from satellite.context.compaction import format_usage, project_usage

        say("")
        say(format_usage(project_usage(project)))


@app.command()
def context(project: Path = typer.Option(Path.cwd(), "--project", help="Project root.")) -> None:
    """Muestra cuánto contexto llevas usado del modelo activo (barra y %)."""
    from satellite.context.compaction import (
        format_usage,
        is_declared,
        project_usage,
        window_config,
    )

    usage = project_usage(project)
    say(format_usage(usage))
    threshold = int(window_config(project).compact_at * 100)
    say(
        f"  real={usage.real_tokens}  estimado={usage.estimated_tokens}  "
        f"ventana={usage.window_tokens} ({usage.window_source})  "
        f"autocompactado al {threshold}%"
    )
    if not is_declared(usage.window_source):
        say(
            "  ! ese modelo no declara su ventana en /models, así que es una "
            "estimación: fija 'llm.compaction.window' en "
            ".satellite/config/config.json con el valor real"
        )
    if usage.percent >= threshold:
        say("  se compactará al enviar el próximo mensaje ('/compact' en la TUI)")
