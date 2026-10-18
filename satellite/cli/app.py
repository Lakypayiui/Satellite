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
session_app = typer.Typer(
    help="Conversaciones guardadas: listar, crear, reanudar, fijar y archivar."
)
git_app = typer.Typer(help="Estado de git y worktrees (lectura + ciclo de worktrees).")
git_worktree_app = typer.Typer(help="Worktrees del repo.")
comments_app = typer.Typer(help="Comentarios inline persistidos (revisión de cambios).")
queue_app = typer.Typer(help="Cola de instrucciones persistida (el siguiente prompt).")
app.add_typer(skills_app, name="skills")
app.add_typer(tools_app, name="tools")
app.add_typer(provider_app, name="provider")
app.add_typer(session_app, name="session")
app.add_typer(git_app, name="git")
app.add_typer(comments_app, name="comments")
app.add_typer(queue_app, name="queue")
git_app.add_typer(git_worktree_app, name="worktree")


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


def _cancel_handler(orchestrator, motivo: str = "cancelado con Ctrl+C"):
    """Ctrl+C pide la cancelación; un segundo Ctrl+C sí corta el proceso.

    El runtime para con lo hecho contado, así que la primera pulsación no debe tirar el
    proceso por la borda; la segunda existe para que nadie se quede atrapado.
    """
    estado = {"veces": 0}

    def handler(_signum=None, _frame=None) -> None:
        estado["veces"] += 1
        cancel = getattr(orchestrator, "cancel", None)
        if estado["veces"] == 1 and callable(cancel):
            cancel("", motivo)
            say("cancelación pedida; Ctrl+C otra vez fuerza la salida")
            return
        raise KeyboardInterrupt

    return handler


@app.command()
def run(
    goal: str = typer.Argument(..., help="The user goal to execute."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
    yes: bool = typer.Option(False, "--yes", help="Auto-aprobar propuestas (sin preguntar)."),
) -> None:
    """Run the orchestrator for a goal and print the summary."""
    orchestrator, _bus, _store, _ = build_runtime(project, allow_desktop=True)
    from satellite.approval.base import AutoApprovalPort
    from satellite.approval.cli import CliApproval
    from satellite.core.task import Task

    import asyncio

    services = getattr(orchestrator, "services", None)
    if services is not None and getattr(services, "approval", None) is None:
        services.approval = AutoApprovalPort() if yes else CliApproval()
    # Ctrl+C no se lleva por delante el proceso: pide cancelar y deja que el runtime pare
    # entre nodos, con lo hecho contado. El segundo Ctrl+C sí corta.
    import signal

    anterior = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _cancel_handler(orchestrator))
    try:
        result = asyncio.run(
            orchestrator.execute(Task(goal=goal, project_root=project.resolve()))
        )
    finally:
        signal.signal(signal.SIGINT, anterior)
    _print_result(result, project=project.resolve())


@app.command()
def plan(
    goal: str = typer.Argument(..., help="Tarea ambigua o a planificar."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Modo plan: propone alternativas (desarrolladas por el LLM) para elegir."""
    import asyncio

    from satellite.orchestration.chat import ChatSession

    orchestrator, _bus, _store, _registry = build_runtime(project, allow_desktop=True)
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
    try:
        from satellite.tui.app import run_tui
    except ModuleNotFoundError as exc:
        # Fallar en claro: lo normal es estar usando otro intérprete (el del sistema
        # en lugar del entorno del proyecto) y un ModuleNotFoundError pelado no lo dice.
        import os

        missing = getattr(exc, "name", "") or "una dependencia"
        venv_python = ".venv\\Scripts\\python" if os.name == "nt" else ".venv/bin/python"
        typer.echo(
            f"la TUI no puede arrancar: falta '{missing}'."
            "\nNecesita las dependencias del proyecto (entre ellas 'textual'):"
            "\n  - instálalas en este intérprete:  python -m pip install -e ."
            f"\n  - o lánzala con el entorno del proyecto:  {venv_python} -m satellite.cli tui",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    run_tui(project)


@app.command()
def agents(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
    create: str = typer.Option(
        "",
        "--create",
        help="Necesidad de capacidad o plantilla (p. ej. desktop_operator).",
    ),
    yes: bool = typer.Option(False, "--yes", help="Auto-aprobar la creación."),
) -> None:
    """List the agent catalog, or create a new agent from a capability need."""
    import asyncio

    from satellite.agents.templates import has_template
    from satellite.approval.base import AutoApprovalPort
    from satellite.approval.cli import CliApproval

    template_id = create.strip().lower() if create else ""
    if template_id and has_template(template_id):
        # Plantilla determinista: no necesita LLM; sí validador y aprobación.
        from satellite.agents.expander import AgentExpander
        from satellite.approval.policy import ApprovalPolicy
        from satellite.cli.runtime_builder import build_agent_catalog

        registry, prompts, catalog_store, validator = build_agent_catalog(project)
        expander = AgentExpander(
            None,
            registry,
            prompts,
            catalog_store=catalog_store,
            policy=ApprovalPolicy(),
            validator=validator,
        )
        port = AutoApprovalPort() if yes else CliApproval()
        outcome = asyncio.run(
            expander.create_template(template_id, approval_port=port)
        )
        detail = (
            outcome.spec.id
            if outcome.spec is not None
            else outcome.reason or outcome.question or outcome.reuse_candidate or ""
        )
        say(f"{outcome.kind}: {detail}")
        return

    orchestrator, _bus, _store, registry = build_runtime(project, allow_desktop=True)
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
            "  Ojo: las primitivas del runtime (run_python, run_argv, read_file, "
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
            orchestrator, _bus, _store, _registry = build_runtime(root, allow_desktop=True)
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
        orchid, _bus, _store, registry = build_runtime(root, allow_desktop=True)
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


@app.command()
def commands(
    json_out: bool = typer.Option(False, "--json", help="Salida JSON del catálogo."),
    gaps: bool = typer.Option(False, "--gaps", help="Sólo los huecos de paridad."),
) -> None:
    """List the shared command/action catalog (one definition for TUI, CLI and app)."""
    import json as _json

    from satellite import commands as catalogo

    if json_out:
        say(
            _json.dumps(
                [spec.to_dict() for spec in catalogo.CATALOG], ensure_ascii=False, indent=2
            )
        )
        return
    if gaps:
        # No es una prueba: es el informe que pide el criterio (ver huecos sin taparlos con botones).
        informe = catalogo.parity()
        say(
            f"catálogo: {informe['commands']} comandos · "
            f"por superficie {informe['per_surface']}"
        )
        for surface, faltan in informe["gaps"].items():
            say(f"  {surface}: {len(faltan)} sin cubrir — {', '.join(faltan) or '(ninguno)'}")
        return
    for spec in catalogo.CATALOG:
        marcas = ",".join(spec.surfaces)
        say(f"  {spec.id:<16} [{marcas}]  {spec.summary}")


# ---------------------------------------------------------------- sesiones


def _session_store(project: Path):
    """Almacén de conversaciones del proyecto (misma operación que la TUI y la app)."""
    from satellite.orchestration.sessions import SessionStore

    return SessionStore(project.resolve())


def _session_marks(info) -> str:
    marks = []
    if info.active:
        marks.append("● activa")
    if info.pinned:
        marks.append("★ fijada")
    if info.archived:
        marks.append("archivada")
    return f"  [{', '.join(marks)}]" if marks else ""


def _require_session(store, target: str) -> str:
    session_id = store.find(target)
    if session_id is None:
        say(f"no existe esa sesión: {target}")
        raise typer.Exit(code=1)
    return session_id


@session_app.command("list")
def session_list(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Lista las conversaciones guardadas (marca la activa, la fijada y la archivada)."""
    infos = _session_store(project).list()
    if not infos:
        say("(sin conversaciones guardadas)")
        return
    for info in infos:
        say(f"{info.id}  {info.name}  ({info.turns} turnos){_session_marks(info)}")


@session_app.command("new")
def session_new(
    name: str = typer.Argument("", help="Nombre de la conversación."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Crea una conversación nueva (vacía) y la deja activa."""
    session_id = _session_store(project).create(name)
    say(f"sesión nueva: {session_id}")


@session_app.command("use")
def session_use(
    target: str = typer.Argument(..., help="Id o nombre de la conversación."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Reanuda una conversación (la deja activa)."""
    session_id = _session_store(project).switch(target)
    if session_id is None:
        say(f"no existe esa sesión: {target}")
        raise typer.Exit(code=1)
    say(f"sesión activa: {session_id}")


@session_app.command("name")
def session_name(
    name: str = typer.Argument(..., help="Nombre nuevo de la conversación activa."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Renombra la conversación activa (conserva sus turnos)."""
    store = _session_store(project)
    if not name.strip():
        say("el nombre no puede estar vacío")
        raise typer.Exit(code=1)
    store.rename(store.active_id(), name.strip())
    say(f"sesión renombrada: {name.strip()}")


def _set_session_flag(
    target: str,
    project: Path,
    *,
    message: str,
    pinned: bool | None = None,
    archived: bool | None = None,
) -> None:
    """Fija/archiva una conversación; al archivar la activa se pasa a otra sin archivar."""
    store = _session_store(project)
    session_id = _require_session(store, target)
    if pinned is not None:
        store.set_pinned(session_id, pinned)
    if archived is not None:
        store.set_archived(session_id, archived)
        if archived and store.active_id() == session_id:
            destino = store.next_active_after_archive(session_id)
            if destino is None:
                say("no queda ninguna conversación sin archivar: sin conversación activa")
            else:
                store.set_active(destino)
                say(f"activa ahora: {destino}")
    say(f"{message}: {session_id}")


@session_app.command("pin")
def session_pin(
    target: str = typer.Argument(..., help="Id o nombre de la conversación."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Fija una conversación (no cambia cuál está activa)."""
    _set_session_flag(target, project, pinned=True, message="sesión fijada")


@session_app.command("unpin")
def session_unpin(
    target: str = typer.Argument(..., help="Id o nombre de la conversación."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Deja de fijar una conversación."""
    _set_session_flag(target, project, pinned=False, message="sesión desfijada")


@session_app.command("archive")
def session_archive(
    target: str = typer.Argument(..., help="Id o nombre de la conversación."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Archiva una conversación (conserva turnos; si era la activa, pasa a otra)."""
    _set_session_flag(target, project, archived=True, message="sesión archivada")


@session_app.command("unarchive")
def session_unarchive(
    target: str = typer.Argument(..., help="Id o nombre de la conversación."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Desarchiva una conversación."""
    _set_session_flag(target, project, archived=False, message="sesión desarchivada")


# ---------------------------------------------------------------- git


@git_app.command("status")
def git_status(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Muestra el estado real del repo (en el worktree activo, si lo hay)."""
    from satellite import gitops

    root = project.resolve()
    objetivo = gitops.active_workdir(root) or root
    try:
        data = gitops.status(objetivo)
    except gitops.GitError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    if objetivo != root:
        say(f"workdir: {objetivo}")
    say(f"rama: {data['branch']}" + (" (detached)" if data["detached"] else ""))
    if data["upstream"]:
        say(f"upstream: {data['upstream']} ↑{data['ahead']} ↓{data['behind']}")
    if data["clean"]:
        say("sin cambios")
        return
    for grupo, titulo in (
        ("conflicts", "conflicto"),
        ("staged", "staged"),
        ("unstaged", "sin preparar"),
        ("untracked", "sin seguimiento"),
    ):
        for entry in data[grupo]:
            say(f"  [{titulo}] {entry['code']} {entry['path']}")


@git_worktree_app.command("list")
def git_worktree_list(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Lista los worktrees y marca el activo como contexto de ejecución."""
    from satellite import gitops

    root = project.resolve()
    if not gitops.is_repo(root):
        say("no es un repositorio git")
        raise typer.Exit(code=1)
    activo = gitops.active_workdir(root)
    for wt in gitops.list_worktrees(root):
        marcas = []
        if wt.get("main"):
            marcas.append("principal")
        if activo and str(activo) == wt["path"]:
            marcas.append("activo")
        sufijo = f"  [{', '.join(marcas)}]" if marcas else ""
        say(f"{wt['branch'] or '(detached)'}{sufijo}")
        say(f"    {wt['path']}")


@git_worktree_app.command("add")
def git_worktree_add(
    name: str = typer.Argument(..., help="Nombre gestionado del worktree."),
    branch: str = typer.Option("", "--branch", help="Rama a crear (por defecto wt/<nombre>)."),
    base_ref: str = typer.Option("", "--from", help="Ref base (por defecto HEAD)."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Crea un worktree gestionado con una rama nueva."""
    from satellite import gitops

    try:
        creado = gitops.add_worktree(project.resolve(), name, branch=branch, base_ref=base_ref)
    except gitops.GitError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    say(f"worktree creado: {creado['path']} (rama {creado['branch']})")


@git_worktree_app.command("use")
def git_worktree_use(
    path: str = typer.Argument(..., help="Ruta del worktree, o 'proyecto' para volver."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Deja un worktree como contexto de ejecución (o vuelve al proyecto)."""
    from satellite import gitops

    root = project.resolve()
    objetivo = "" if path.strip().lower() in ("proyecto", "project") else path
    try:
        efectivo = gitops.set_active_workdir(root, objetivo or None)
    except gitops.GitError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    say(f"ejecutando en: {efectivo or root}")


@git_worktree_app.command("remove")
def git_worktree_remove(
    path: str = typer.Argument(..., help="Ruta del worktree gestionado."),
    force: bool = typer.Option(False, "--force", help="Descarta cambios sin confirmar."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Quita un worktree gestionado (rechaza cambios sin confirmar salvo --force)."""
    from satellite import gitops

    try:
        quitado = gitops.remove_worktree(project.resolve(), path, force=force)
    except gitops.GitError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    say(f"worktree quitado: {quitado.get('removed', path)}")


# ---------------------------------------------------------------- comentarios


def _parse_line_range(raw: str) -> tuple[int, int]:
    """``3`` o ``3-5`` → ``(inicio, fin)``; falla en claro si no es válido."""
    texto = (raw or "").strip()
    if not texto:
        raise ValueError("falta el rango de líneas (p. ej. 3 o 3-5)")
    partes = texto.split("-", 1)
    try:
        inicio = int(partes[0])
        fin = int(partes[1]) if len(partes) > 1 and partes[1] else inicio
    except ValueError as exc:
        raise ValueError("rango inválido: usa <inicio> o <inicio>-<fin>") from exc
    if inicio < 1 or fin < 1 or fin < inicio:
        raise ValueError("rango inválido: empieza en 1 y el fin no puede ser menor")
    return inicio, fin


def _active_session_id(root: Path) -> str:
    """Sesión activa del proyecto (para vincular el comentario), sin armar runtime."""
    from satellite.orchestration.sessions import SessionStore

    return SessionStore(root).active_id()


@comments_app.command("list")
def comments_list(
    file: str = typer.Option("", "--file", help="Filtrar por archivo."),
    task_id: str = typer.Option("", "--task", help="Filtrar por tarea."),
    session_id: str = typer.Option("", "--session", help="Filtrar por sesión."),
    open_only: bool = typer.Option(False, "--open", help="Sólo los no resueltos."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Lista los comentarios inline persistidos."""
    from satellite import comments as comments_store

    items = comments_store.list_comments(
        project.resolve(),
        file=file or None,
        task_id=task_id or None,
        session_id=session_id or None,
        include_resolved=not open_only,
    )
    if not items:
        say("(sin comentarios)")
        return
    for item in items:
        estado = "resuelto" if item["resolved"] else "abierto"
        rango = (
            f"{item['line_start']}-{item['line_end']}"
            if item["line_end"] != item["line_start"]
            else str(item["line_start"])
        )
        say(f"{item['id']}  {item['file']}:{rango}  [{estado}] [{item['side']}]  {item['text']}")


@comments_app.command("add")
def comments_add(
    file: str = typer.Argument(..., help="Archivo relativo al proyecto."),
    lines: str = typer.Argument(..., help="Línea o rango: 3 o 3-5."),
    text: str = typer.Argument(..., help="Texto del comentario (entre comillas si lleva espacios)."),
    side: str = typer.Option("new", "--side", help="new | old."),
    task_id: str = typer.Option("", "--task", help="Tarea vinculada (opcional)."),
    session_id: str = typer.Option("", "--session", help="Sesión (por defecto la activa)."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Crea un comentario inline con archivo, lado y rango de líneas explícitos."""
    from satellite import comments as comments_store

    root = project.resolve()
    try:
        inicio, fin = _parse_line_range(lines)
        item = comments_store.add_comment(
            root,
            file=file,
            line_start=inicio,
            line_end=fin,
            text=text,
            side=side,
            task_id=task_id,
            session_id=session_id or _active_session_id(root),
        )
    except ValueError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    say(f"comentario {item['id']}: {item['file']}:{item['line_start']}-{item['line_end']}")


def _set_comment_resolved(comment_id: str, resolved: bool, project: Path) -> None:
    from satellite import comments as comments_store

    try:
        comments_store.update_comment(project.resolve(), comment_id, resolved=resolved)
    except KeyError as exc:
        say(f"no existe el comentario {comment_id}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    say(f"comentario {comment_id}: {'resuelto' if resolved else 'reabierto'}")


@comments_app.command("resolve")
def comments_resolve(
    comment_id: str = typer.Argument(..., help="Id del comentario."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Marca un comentario como resuelto."""
    _set_comment_resolved(comment_id, True, project)


@comments_app.command("reopen")
def comments_reopen(
    comment_id: str = typer.Argument(..., help="Id del comentario."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Reabre un comentario resuelto."""
    _set_comment_resolved(comment_id, False, project)


@comments_app.command("delete")
def comments_delete(
    comment_id: str = typer.Argument(..., help="Id del comentario."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Borra un comentario persistido."""
    from satellite import comments as comments_store

    if not comments_store.delete_comment(project.resolve(), comment_id):
        say(f"no existe el comentario {comment_id}")
        raise typer.Exit(code=1)
    say(f"comentario {comment_id} borrado")


# ---------------------------------------------------------------- cola


@queue_app.command("list")
def queue_list(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Lista la cola de instrucciones (la primera es la siguiente)."""
    from satellite import prompt_queue

    items = prompt_queue.list_items(project.resolve())
    if not items:
        say("(cola vacía)")
        return
    for index, item in enumerate(items, 1):
        marca = " [plan]" if item["plan_mode"] else ""
        say(f"{index}. {item['id']}  {item['text']}{marca}")


@queue_app.command("add")
def queue_add(
    text: str = typer.Argument(..., help="Instrucción a encolar (entre comillas si lleva espacios)."),
    plan: bool = typer.Option(False, "--plan", help="Ejecutarla en modo plan."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Encola una instrucción para el turno siguiente (no interrumpe el actual)."""
    from satellite import prompt_queue

    try:
        prompt_queue.enqueue(project.resolve(), text, plan_mode=plan)
    except ValueError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    say(f"encolado: {text}")


@queue_app.command("edit")
def queue_edit(
    item_id: str = typer.Argument(..., help="Id del item en cola."),
    text: str = typer.Argument(..., help="Nuevo texto (entre comillas si lleva espacios)."),
    plan: bool = typer.Option(False, "--plan", help="Marcar como modo plan."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Edita un item aún no ejecutado (afecta a un turno futuro, no al que corre)."""
    from satellite import prompt_queue

    try:
        prompt_queue.update_item(project.resolve(), item_id, text=text, plan_mode=plan or None)
    except KeyError as exc:
        say(f"no existe el item {item_id}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    say(f"editado: {item_id}")


@queue_app.command("remove")
def queue_remove(
    item_id: str = typer.Argument(..., help="Id del item en cola."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Quita un item de la cola (no ejecuta nada)."""
    from satellite import prompt_queue

    try:
        prompt_queue.remove_item(project.resolve(), item_id)
    except KeyError as exc:
        say(f"no existe el item {item_id}")
        raise typer.Exit(code=1) from exc
    say(f"quitado: {item_id}")


@queue_app.command("clear")
def queue_clear(
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Vacía la cola por completo."""
    from satellite import prompt_queue

    prompt_queue.clear(project.resolve())
    say("cola vacía")


@queue_app.command("run")
def queue_run(
    yes: bool = typer.Option(False, "--yes", help="Auto-aprobar propuestas (sin preguntar)."),
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Ejecuta la cola en orden: un prompt por turno, con el flujo real de envío."""
    import asyncio

    from satellite import prompt_queue

    root = project.resolve()
    if not prompt_queue.list_items(root):
        say("(cola vacía)")
        return

    from satellite.approval.base import AutoApprovalPort
    from satellite.approval.cli import CliApproval
    from satellite.cli.runtime_builder import build_runtime
    from satellite.orchestration.chat import ChatSession

    try:
        orchestrator, _bus, _store, _registry = build_runtime(root, allow_desktop=True)
    except Exception as exc:  # noqa: BLE001 — sin proveedor no hay turno
        say(f"no se pudo arrancar el runtime: {exc}")
        raise typer.Exit(code=1) from exc
    approval = AutoApprovalPort() if yes else CliApproval()
    chat = ChatSession(root, orchestrator)
    chat.load()

    async def drenar() -> None:
        while True:
            item = prompt_queue.pop_next(root)
            if item is None:
                break
            say(f"› {item['text']}")
            try:
                reply = await chat.send(
                    item["text"],
                    approval=approval,
                    plan_mode=bool(item.get("plan_mode")),
                )
            except Exception as exc:  # noqa: BLE001 — se dice y se sigue con el siguiente
                say(f"  error: {exc}")
                continue
            say(f"  {reply.status}: {reply.text}")
            if reply.error:
                say(f"  error: {reply.error}")
            plan_options = (reply.output or {}).get("plan_options")
            if isinstance(plan_options, dict):
                recommendation = plan_options.get("recommendation", "")
                for index, option in enumerate(plan_options.get("options", []), 1):
                    if not isinstance(option, dict):
                        continue
                    mark = " ★ recomendada" if option.get("id") == recommendation else ""
                    say(f"    {index}. {option.get('title')}{mark}")
                    if option.get("summary"):
                        say(f"       {option.get('summary')}")

    asyncio.run(drenar())
    say("cola vacía")
