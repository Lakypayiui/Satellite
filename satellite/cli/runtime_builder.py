"""Runtime builder: compone el runtime completo desde un project root.

Carga los 3 agentes base (verificados por hash contra el paquete), recarga de
forma tolerante el catálogo global persistido, y conecta tools, skills,
sandbox, context engine, planner, runner, orchestrator, expander y
puerto de aprobación.
"""

from __future__ import annotations

import json
from pathlib import Path

from satellite.agents.creator import AgentProposalGenerator
from satellite.agents.expander import AgentExpander
from satellite.agents.factory import DefaultAgentFactory
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.store import AgentCatalogStore
from satellite.agents.validator import AgentProposalValidator
from satellite.approval.base import AutoApprovalPort
from satellite.approval.cli import CliApproval
from satellite.approval.policy import ApprovalPolicy
from satellite.context.engine import DefaultContextEngine
from satellite.context.preprocessor import LocalPreprocessor
from satellite.core.tempfiles import cleanup_orphan_temps
from satellite.cli.console import say
from satellite.config import providers as provider_config
from satellite.core.errors import ProviderError
from satellite.core.events import EventType, ExecutionEvent
from satellite.events.bus import EventBus
from satellite.desktop_control.config import load_desktop_settings
from satellite.desktop_control.policy import DesktopControlPolicy
from satellite.desktop_control.vision import vision_fallback_for
from satellite.kernel.audit import AuditLog
from satellite.kernel.manifest import RESERVED_AGENT_IDS, verify_and_restore
from satellite.kernel.migrations import migrate, migrate_agent_specs, migrate_prompt_files
from satellite.kernel.paths import ensure_dirs, resolve_paths
from satellite.kernel.policy import PolicyMode, Settings, load_settings
from satellite.metrics.pricing import PriceBook
from satellite.observability.call_log import CallRecorder, default_log_dir
from satellite.observability.logger import ExecutionLogger
from satellite.providers.metered import MeteredProvider
from satellite.safe_proxy.interceptor import SanitizingProvider, sanitize_provider
from satellite.skills.cache import SkillResultCache
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.delegation import DelegationManager
from satellite.orchestration.desktop_flow import DesktopFlow
from satellite.orchestration.diagram_flow import DiagramFlow
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.orchestration.plan_mode import PlanOptionGenerator
from satellite.orchestration.planner import LLMPlanner
from satellite.orchestration.runner import DefaultGraphRunner
from satellite.persistence.store import FileStore
from satellite.providers.base import LLMProvider
from satellite.providers.factory import ProviderConfig, ProviderFactory
from satellite.providers.caching import parse_cache_config
from satellite.providers.capabilities import EFFORT_PARAM, param_for_control
from satellite.sandbox.levels import resolve_sandbox
from satellite.security.policy import DefaultSecurityPolicy
from satellite.skills.registry import SkillRegistry
from satellite.skills.runner import SkillRunner
from satellite.tools.context_tool import RequestContextTool
from satellite.tools.delegate import DelegateTool
from satellite.tools.spawn_agent import SpawnAgentTool
from satellite.tools.diagram import RenderDiagramTool
from satellite.tools.fs import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from satellite.tools.git import GitStatusTool
from satellite.tools.primitives import RunPythonTool, RunArgvTool, SearchFilesTool
from satellite.tools.jobs import JobTool
from satellite.tools.registry import DefaultToolRegistry
from satellite.tools.skills import ProposeSkillTool, SearchSkillsTool
from satellite.tools.terminal import RunCommandTool
from satellite.tool_manifest import TOOL_IDS as DESKTOP_TOOL_IDS

DEFAULT_ALLOWED_TOOLS: set[str] = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "request_context",
    "run_python",
    "run_argv",
    "run_command",
    "job",
    "search_files",
    "git_status",
    "search_skills",
    "propose_skill",
    "delegate",
    "spawn_agent",
    "render_diagram",
}

AGENTS_SUBDIR = Path("agents")
CONFIG_PATH = Path(".satellite") / "config" / "config.json"

_SAFE_MODE = False


def set_safe_mode(enabled: bool) -> None:
    """Activa/desactiva el modo seguro (sólo agentes base y primitivas)."""
    global _SAFE_MODE
    _SAFE_MODE = bool(enabled)


def build_runtime(
    project_root: Path,
    *,
    approval: object | None = None,
    allow_desktop: bool = False,
):
    """Construye (orchestrator, bus, store, registry) para un project root.

    ``allow_desktop`` registra las tools de control de escritorio **sólo** en
    superficies con humano (CLI/TUI/Desktop). MCP llama con ``False`` (default).
    """
    project_root = Path(project_root).resolve()
    base_dir = Path(__file__).parent.parent / "agents"
    paths = ensure_dirs(resolve_paths(project_root))
    # Remove `.tmp` files left behind by a crash/kill. Recent ones are untouched:
    # another process could still be writing them.
    cleanup_orphan_temps(
        [paths.global_root] + ([paths.project_data] if paths.project_data else [])
    )
    # Migraciones de esquema del directorio de datos (idempotente): sin esto el
    # proyecto queda en v0 y `doctor` falla hasta que alguien corra `init`.
    if paths.project_data is not None:
        migrate(paths.project_data)

    # Kernel: los specs/prompts base se sincronizan y verifican por hash.
    verify_and_restore(base_dir / "specs", paths.agents_root, patterns=("*.json",))
    verify_and_restore(base_dir / "prompts", paths.agents_root, patterns=("*.txt",))
    # Migración tolerante del layout antiguo (<id>.txt → par system/user).
    migrate_prompt_files(paths.agents_root)
    # Esquema v4: los specs guardados sin `lifecycle` quedan normalizados.
    migrate_agent_specs(paths.agents_root)
    catalog_store = AgentCatalogStore(paths.agents_root)

    settings = load_settings(project_root / CONFIG_PATH)

    # Seguridad + tools. El terminal queda activado para que decida el permiso
    # (una vez / la sesión / rechazar): con allow_terminal=False sería un veto duro.
    # La allowlist sale de `terminal.allowed_commands` y vacía por defecto: sin ella todo
    # comando se pregunta.
    allowed_tools = set(DEFAULT_ALLOWED_TOOLS)
    # Doble llave: la superficie debe permitir humano (CLI/TUI/Desktop; MCP nunca)
    # y el usuario debe tener «enable desktop control» encendido en Ajustes. Si
    # está apagado, no se registran tools, ni agente, ni ruta de escritorio.
    desktop_settings = load_desktop_settings()
    desktop_active = bool(allow_desktop and desktop_settings.enabled)
    if desktop_active:
        allowed_tools |= set(DESKTOP_TOOL_IDS)
    policy = DefaultSecurityPolicy(
        allowed_tools=allowed_tools,
        allow_terminal=True,
        allowed_commands=settings.allowed_commands,
    )
    # Control de escritorio: política (config local deny-by-default) + auditoría.
    desktop_policy = (
        DesktopControlPolicy(
            desktop_settings,
            # El modo unrestricted es una decisión humana explícita: no se pregunta.
            allow_auto_approval=settings.policy_mode is PolicyMode.UNRESTRICTED,
        )
        if desktop_active
        else None
    )
    desktop_audit = AuditLog(paths.audit_root) if desktop_active else None
    desktop_vision = vision_fallback_for(project_root) if desktop_active else None
    tools = build_tool_registry(
        policy,
        allow_desktop=desktop_active,
        desktop_policy=desktop_policy,
        desktop_audit=desktop_audit,
        desktop_vision=desktop_vision,
    )

    bus = EventBus()
    provider = _safe_proxy(
        _metered(_load_provider(project_root), project_root=project_root, settings=settings)
    )

    # Skills globales (carga tolerante) + sandbox del nivel configurado.
    # El nivel manda para skills Y para los procesos del agente (``run_command``/
    # ``run_python``): N0 subproceso, N1 Docker (falla cerrado si no está), N2 host.
    skill_registry = SkillRegistry(paths.skills_root, sink=None)
    skill_registry.load_all()
    sandbox = resolve_sandbox(settings.sandbox_level)
    skill_runner = SkillRunner(
        skill_registry,
        sandbox,
        cache=SkillResultCache(_skill_cache_path(paths)),
    )

    # Catálogo: base (ya sincronizado) + agentes creados por el usuario.
    loaded_specs = {spec.id: spec for spec in catalog_store.load_specs()}
    # El operador de escritorio sólo existe si el control está habilitado en
    # Ajustes y la superficie tiene humano. Si no, ni se ve ni se puede ejecutar.
    if not desktop_active:
        loaded_specs.pop("desktop_operator", None)
    if _SAFE_MODE:
        base_ids = set(RESERVED_AGENT_IDS)
        loaded_specs = {k: v for k, v in loaded_specs.items() if k in base_ids}
        bus.emit(
            ExecutionEvent(
                event_type=EventType.SAFE_MODE_ENTERED,
                task_id="system",
                payload={"agents": sorted(loaded_specs)},
            )
        )
    registry = DefaultAgentRegistry(loaded_specs)
    prompt_reg = DefaultPromptRegistry()
    for prompt_id, (system, user) in catalog_store.load_prompts().items():
        prompt_reg.register_pair(prompt_id, system, user)

    context = DefaultContextEngine(project_root)

    approval_port = approval if approval is not None else _default_approval(settings)

    # Modo plan: alternativas desarrolladas por el LLM.
    plan_advisor = PlanOptionGenerator(provider, prompt_reg)

    # Providers per role, built lazily and cached: every node creation asks for one.
    role_providers: dict[str, LLMProvider] = {}

    def provider_for_role(role: str) -> LLMProvider:
        key = role or "agents"
        if key not in role_providers:
            role_providers[key] = _role_provider(project_root, key, provider)
        return role_providers[key]

    # Children inherit the parent's provider unless the user configured a specific
    # model for subagents. ``None`` here means "inherit"; it is not a global fallback.
    subagent_provider = _optional_role_provider(project_root, "subagents")
    if subagent_provider is not None:
        subagent_provider = _safe_proxy(
            _metered(
                subagent_provider,
                project_root=project_root,
                settings=settings,
                role="subagents",
            )
        )

    factory = DefaultAgentFactory(
        registry=registry,
        prompts=prompt_reg,
        tools=tools,
        provider=provider,
        policy=policy,
        context_engine=context,
        project_root=project_root,
        # Worktree activo (si lo hay): las tools escriben y ejecutan ahí, no en el
        # workspace original. ``None`` = el proyecto de siempre.
        workdir=_active_workdir(project_root),
        # Sandbox del nivel configurado: los agentes ejecutan sus procesos por él.
        sandbox=sandbox,
        # Shared skill catalog: `search_skills`/skill execution must see it.
        skills=skill_registry,
        # A node with ``model_role`` uses the provider configured for that role
        # (``llm.agents`` by default) instead of always the global one.
        provider_resolver=provider_for_role,
        # Optional child model (``llm.subagents``). The parent and orchestrator are
        # untouched; only children use it.
        subagent_provider=subagent_provider,
    )
    delegation = DelegationManager(
        factory,
        registry=registry,
        sink=bus,
        approval=approval_port,
    )
    factory.delegation_manager = delegation

    # Creator + validador + expander (rol de modelo propio por config).
    creator_provider = _safe_proxy(
        _metered(
            _role_provider(project_root, "creator", provider),
            project_root=project_root,
            settings=settings,
            role="creator",
        )
    )
    validator = AgentProposalValidator(
        registry,
        tool_ids={tool.id for tool in tools.all()},
        allowed_roles=("generated",),
        max_prompt_chars=settings.quotas.max_prompt_chars,
    )
    creator = AgentProposalGenerator(
        creator_provider, registry, prompt_reg, tools, validator=validator
    )
    expander = AgentExpander(
        creator=creator,
        registry=registry,
        prompts=prompt_reg,
        catalog_store=catalog_store,
        policy=ApprovalPolicy(settings.policy_mode),
        sink=bus,
        skills=skill_registry,
    )

    store = FileStore(project_root)
    from satellite.orchestration.cancel import CancelToken

    services = RuntimeServices(
        registry=registry,
        factory=factory,
        tools=tools,
        context=context,
        provider=provider,
        sink=bus,
        approval=approval_port,
        cancel=CancelToken(),
        skills=skill_registry,
        sandbox=sandbox,
        delegation=delegation,
    )
    runner = DefaultGraphRunner(factory=factory, context_engine=context, sink=bus)
    runner.logger = ExecutionLogger(project_root / ".satellite" / "observability")
    planner = LLMPlanner(provider, registry, prompts=prompt_reg)
    orchestrator = DefaultOrchestrator(
        planner,
        runner,
        services,
        store=store,
        sink=bus,
        expander=expander,
        plan_advisor=plan_advisor,
        allow_chat_direct=settings.direct_reply,
    )
    orchestrator.preprocessor = _build_preprocessor(
        project_root,
        provider_for_role("context") if settings.refine_goal else provider,
        settings,
    )
    orchestrator.skill_runner = skill_runner
    # Capacidad condicional: diagramas/arquitecturas se enrutan al agente de diagrama
    # (pele_diagram_agent). El flujo sólo delega; el agente diseña el Mermaid y lo
    # renderiza con la tool render_diagram. Si el renderer no está instalado, el agente
    # devuelve la fuente Mermaid y el flujo la presenta sin romper la tarea.
    orchestrator.diagram_flow = DiagramFlow(
        factory=factory,
        project_root=project_root,
        services=services,
    )
    # Control del escritorio: sólo con humano y con el flag de Ajustes encendido.
    if desktop_active:
        orchestrator.desktop_flow = DesktopFlow(
            factory=factory,
            project_root=project_root,
            services=services,
        )
    return orchestrator, bus, store, registry


def _safe_proxy(provider: LLMProvider) -> LLMProvider:
    """Modo interno del Safe Proxy: sanea el ciclo del proveedor (idempotente).

    Envuelve el proveedor (ya medido) con la política del Safe Proxy **en el mismo
    proceso**: no hay listener, ni puerto, ni roundtrip HTTP. Si el modo interno no está
    activo (o su interruptor está apagado) el decorador delega tal cual: bypass claro.
    El modo servidor **no** se envuelve aquí, para no sanear dos veces.
    """
    return sanitize_provider(provider)


def _default_approval(settings: Settings) -> object:
    """Puerto por defecto: pregunta en consola salvo en modos auto/unrestricted."""
    if settings.policy_mode is PolicyMode.ASK:
        return CliApproval()
    return AutoApprovalPort()


def _build_preprocessor(
    project_root: Path, provider: LLMProvider, settings: Settings
) -> LocalPreprocessor | None:
    """Preprocesado opcional (refine_goal), desactivado por defecto."""
    if not settings.refine_goal:
        return None
    try:
        return LocalPreprocessor(provider, project_root)
    except Exception:
        return None


def _skill_cache_path(paths) -> Path | None:
    """Ruta del caché de resultados de skills (``None`` si no hay proyecto)."""
    if paths.project_data is None:
        return None
    return Path(paths.project_data) / "state" / "skill_cache.json"


def _active_workdir(project_root: Path) -> Path | None:
    """Workdir de ejecución (worktree activo). Tolerante: ``None`` si no hay o falla.

    No se deja que un problema de git impida armar el runtime: sin worktree se ejecuta en el
    proyecto, que es exactamente el comportamiento de siempre.
    """
    try:
        from satellite.gitops import active_workdir

        return active_workdir(project_root)
    except Exception:  # noqa: BLE001
        return None


def build_tool_registry(
    policy: DefaultSecurityPolicy | None = None,
    *,
    allow_desktop: bool = False,
    desktop_policy: DesktopControlPolicy | None = None,
    desktop_audit: AuditLog | None = None,
    desktop_vision: object | None = None,
) -> DefaultToolRegistry:
    """Registro de herramientas del runtime (primitivas del kernel incluidas).

    Se expone aparte para poder listarlas desde el CLI sin levantar el runtime
    completo (y por tanto sin necesitar un proveedor configurado).

    ``allow_desktop=False`` por defecto: las tools de control de escritorio sólo se
    registran en superficies con humano (CLI/TUI/Desktop) y **nunca** en MCP.
    """
    if policy is None:
        # La misma política que el runtime: si aquí el terminal estuviera vetado, listar
        # las herramientas y ejecutarlas dirían cosas distintas.
        allowed = set(DEFAULT_ALLOWED_TOOLS)
        if allow_desktop:
            allowed |= set(DESKTOP_TOOL_IDS)
        policy = DefaultSecurityPolicy(allowed_tools=allowed, allow_terminal=True)
    tools = DefaultToolRegistry(policy=policy, desktop_policy=desktop_policy)
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirTool(),
        RunCommandTool(),
        RunArgvTool(),
        JobTool(),
        RunPythonTool(),
        SearchFilesTool(),
        GitStatusTool(),
        RequestContextTool(),
        DelegateTool(),
        SpawnAgentTool(),
        SearchSkillsTool(),
        ProposeSkillTool(),
        RenderDiagramTool(),
    ):
        tools.register(tool)
    if allow_desktop:
        # Import local: mantiene el paquete de tools ligero hasta que se usa.
        from satellite.tools.desktop_control import (
            DesktopActTool,
            DesktopListWindowsTool,
            DesktopObserveTool,
        )

        tools.register(
            DesktopListWindowsTool(policy=desktop_policy, audit=desktop_audit)
        )
        tools.register(DesktopObserveTool(policy=desktop_policy, audit=desktop_audit, vision=desktop_vision))
        tools.register(DesktopActTool(policy=desktop_policy, audit=desktop_audit))
    return tools


def build_agent_catalog(project_root: Path):
    """Registro/prompts/store/validator **sin LLM** (plantillas deterministas).

    Crear una plantilla (`satellite agents --create desktop_operator`) no necesita
    proveedor, pero sí el mismo validador determinista y el mismo catálogo global.
    Devuelve ``(registry, prompts, catalog_store, validator)``.
    """
    project_root = Path(project_root).resolve()
    paths = ensure_dirs(resolve_paths(project_root))
    cleanup_orphan_temps(
        [paths.global_root] + ([paths.project_data] if paths.project_data else [])
    )
    base_dir = Path(__file__).parent.parent / "agents"
    verify_and_restore(base_dir / "specs", paths.agents_root, patterns=("*.json",))
    verify_and_restore(base_dir / "prompts", paths.agents_root, patterns=("*.txt",))
    migrate_prompt_files(paths.agents_root)
    migrate_agent_specs(paths.agents_root)
    catalog_store = AgentCatalogStore(paths.agents_root)
    specs = {spec.id: spec for spec in catalog_store.load_specs()}
    desktop_enabled = bool(load_desktop_settings().enabled)
    if not desktop_enabled:
        specs.pop("desktop_operator", None)
    registry = DefaultAgentRegistry(specs)
    prompts = DefaultPromptRegistry()
    for prompt_id, (system, user) in catalog_store.load_prompts().items():
        prompts.register_pair(prompt_id, system, user)
    tools = build_tool_registry(allow_desktop=desktop_enabled)
    validator = AgentProposalValidator(
        registry,
        tool_ids={tool.id for tool in tools.all()},
        allowed_roles=("generated",),
    )
    return registry, prompts, catalog_store, validator


def _metered(
    provider: LLMProvider,
    *,
    project_root: Path,
    settings: Settings,
    role: str | None = None,
) -> LLMProvider:
    """Envuelve el proveedor para medir llamadas (idempotente).

    Registra hashes y contadores en ``.satellite/observability/calls.jsonl``,
    carga los precios de ``llm.pricing`` y aplica ``llm.max_tokens.<rol>``.
    """
    if isinstance(provider, (MeteredProvider, SanitizingProvider)):
        return provider
    data = provider_config.load_config(project_root)
    cfg = _provider_config_from(data, role=role) or _provider_config_from(data)
    llm = data.get("llm") if isinstance(data, dict) else None
    cache = parse_cache_config(llm.get("cache") if isinstance(llm, dict) else None)
    return MeteredProvider(
        provider,
        CallRecorder(default_log_dir(project_root)),
        provider_name=(cfg.name if cfg is not None else ""),
        model=(cfg.model if cfg is not None else ""),
        price_book=PriceBook.from_config(data),
        max_tokens_by_role=settings.max_tokens,
        prewarm=bool(cache.get("prewarm")),
    )


def _load_provider(project_root: Path) -> LLMProvider:
    """Carga el proveedor global desde ``.satellite/config/config.json``.

    Precedencia: rol ``llm.orchestrator`` → top-level. Los providers guardados
    se resuelven por id.
    """
    config_path = project_root / CONFIG_PATH
    if config_path.exists():
        try:
            from satellite.config.providers import runtime_config

            data = runtime_config(project_root)
            cfg = _provider_config_from(data, role="orchestrator") or _provider_config_from(data)
            if cfg is None:
                raise ProviderError("config sin proveedor")
            return ProviderFactory.create(cfg)
        except Exception as exc:
            raise ProviderError(
                f"config de proveedor inválida en {config_path}: {exc}. "
                "Configura un proveedor válido con 'satellite provider'."
            ) from exc
    # A provider selected globally is usable even if the current project has no
    # local config file yet.
    from satellite.config.providers import runtime_config

    data = runtime_config(project_root)
    cfg = _provider_config_from(data, role="orchestrator") or _provider_config_from(data)
    if cfg is not None:
        return ProviderFactory.create(cfg)
    raise ProviderError(
        f"no hay proveedor global configurado: {config_path}. "
        "Configura uno desde satellite provider o desde Ajustes globales."
    )


def _optional_role_provider(project_root: Path, role: str) -> LLMProvider | None:
    """Provider for ``llm.<role>`` only when that role is explicitly configured.

    Used for subagents: no configuration means the child inherits its parent's
    provider, not the global default.
    """
    config_path = project_root / CONFIG_PATH
    if not config_path.exists():
        return None
    try:
        from satellite.config.providers import runtime_config

        data = runtime_config(project_root)
        llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
        if not isinstance(llm.get(role), dict) or not llm.get(role):
            return None
        cfg = _provider_config_from(data, role=role)
        if cfg is None:
            return None
        return ProviderFactory.create(cfg)
    except Exception:  # noqa: BLE001 — a broken optional role must not break the runtime
        return None


def _role_provider(project_root: Path, role: str, default: LLMProvider) -> LLMProvider:
    """Proveedor del rol indicado; cae al global si no está configurado."""
    config_path = project_root / CONFIG_PATH
    if not config_path.exists():
        return default
    try:
        from satellite.config.providers import runtime_config

        data = runtime_config(project_root)
        cfg = _provider_config_from(data, role=role)
        if cfg is None:
            return default
        return ProviderFactory.create(cfg)
    except Exception:
        return default


def _max_tokens_for(llm: dict, provider: dict | None = None, role: str = "") -> int | None:
    """Tope de salida: rol en ``llm.max_tokens`` → proveedor guardado → escalar."""
    configured = llm.get("max_tokens")
    role_value: object = None
    global_value: object = None
    if isinstance(configured, dict):
        if role:
            role_value = configured.get(role)
        if role_value is None:
            role_value = configured.get("*")
    elif configured is not None:
        global_value = configured
    provider_value = provider.get("max_tokens") if isinstance(provider, dict) else None
    for value in (role_value, provider_value, global_value):
        if value is None:
            continue
        try:
            limit = int(value)
        except (TypeError, ValueError):
            continue
        # Provider settings persist 0 to mean "no explicit limit". Passing that
        # through to OpenAI-compatible APIs requests zero output tokens, which looks
        # exactly like a successful but empty model response in chat and summaries.
        if limit > 0:
            return limit
    return None


def _model_overrides(llm: dict, provider: dict | None = None) -> dict:
    """Capacidades por modelo: ``llm.models`` + las del proveedor guardado."""
    result: dict = {}
    for source in (llm.get("models"), (provider or {}).get("models")):
        if isinstance(source, dict):
            result.update(source)
    return result


def _effort_from(*sections) -> str | None:
    """Nivel de esfuerzo pedido: el primero que lo traiga y no sea ``auto``.

    ``auto`` (y el vacío) significan «no indicar nada»: se deja al proveedor. El valor se
    normaliza a minúsculas; si el modelo no lo admite, el provider no lo envía.
    """
    for section in sections:
        if not isinstance(section, dict):
            continue
        value = str(section.get("reasoning_effort") or "").strip().lower()
        if value and value != "auto":
            return value
    return None


def _think_from(*sections) -> bool | None:
    """Interruptor Think pedido (el primero que lo traiga como booleano), o ``None``.

    ``None`` = no tocar: el adapter no envía el canal Think. Un booleano explícito sí lo
    envía, pero **sólo** si el modelo declara ese canal (lo decide ``resolve_think``).
    """
    for section in sections:
        if not isinstance(section, dict):
            continue
        value = section.get("think")
        if isinstance(value, bool):
            return value
    return None


def _role_overrides(llm: dict, provider: dict | None, role_cfg: dict | None, model: str) -> dict:
    """Capacidades del modelo para ese rol, aisladas del resto.

    A las capacidades generales (``llm.models`` + las del proveedor) se les suma, **sólo para
    este rol**, los niveles que su endpoint anunció (``llm.<rol>.effort_levels``) y el canal
    de esos niveles (``llm.<rol>.effort_control``): así el adapter usa el parámetro correcto
    (p. ej. ``reasoning_effort``) y no manda uno ajeno al modelo.
    """
    overrides = _model_overrides(llm, provider)
    levels = (role_cfg or {}).get("effort_levels")
    control = str((role_cfg or {}).get("effort_control") or "").strip()
    if isinstance(levels, (list, tuple)) and levels and model:
        limpios = [str(level).strip() for level in levels if str(level).strip()]
        if limpios:
            entrada = dict(overrides.get(model) or {})
            entrada["effort_levels"] = limpios
            if control:
                entrada["effort_control"] = control
                entrada["effort_param"] = param_for_control(control) or EFFORT_PARAM
            else:
                entrada.setdefault("effort_param", EFFORT_PARAM)
            overrides = {**overrides, model: entrada}
    return overrides


def _active_provider_entry(data: dict) -> dict:
    """Entrada del proveedor guardado que el runtime usa por defecto (``llm.provider_id``).

    Sirve para que el camino global (sin rol) lea sus ajustes por proveedor —tope de salida y
    capacidades por modelo— en vez de mirar sólo el nivel superior. ``{}`` si no hay guardado.
    """
    providers = data.get("providers") if isinstance(data.get("providers"), list) else []
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    provider_id = str(llm.get("provider_id") or "")
    if provider_id:
        for entry in providers:
            if isinstance(entry, dict) and str(entry.get("id")) == provider_id:
                return entry
    name, model = str(data.get("name") or ""), str(data.get("model") or "")
    for entry in providers:
        if (
            isinstance(entry, dict)
            and entry.get("name") == name
            and entry.get("model") == model
        ):
            return entry
    return {}


def _provider_config_from(data: dict, role: str | None = None) -> ProviderConfig | None:
    """Construye ProviderConfig desde el config, priorizando el rol indicado."""
    llm = data.get("llm", {}) if isinstance(data.get("llm"), dict) else {}
    # `llm.cache`: modo, diagnóstico, pre-calentado y límites del adaptador.
    cache_options = parse_cache_config(llm.get("cache"))
    if role and isinstance(llm.get(role), dict):
        role_cfg = llm[role]
        ref = str(role_cfg.get("provider", ""))
        saved = data.get("providers", []) if isinstance(data.get("providers"), list) else []
        saved_match = next(
            (p for p in saved if isinstance(p, dict) and p.get("id") == ref), None
        )
        if saved_match is not None:
            model = role_cfg.get("model") or saved_match.get("model", "")
            return ProviderConfig(
                name=saved_match.get("name", "openai"),
                model=model,
                base_url=saved_match.get("base_url", ""),
                api_key=saved_match.get("api_key"),
                max_tokens=_max_tokens_for(llm, saved_match, role),
                model_overrides=_role_overrides(llm, saved_match, role_cfg, model),
                reasoning_effort=_effort_from(role_cfg, saved_match),
                thinking=_think_from(role_cfg, saved_match),
                cache_options=cache_options,
                auth_type=str(saved_match.get("auth_type") or ""),
            )
        if role_cfg.get("base_url") or role_cfg.get("model") or role_cfg.get("provider"):
            model = role_cfg.get("model", "gpt-4o-mini")
            return ProviderConfig(
                name=role_cfg.get("provider", "openai"),
                model=model,
                base_url=role_cfg.get("base_url"),
                api_key=role_cfg.get("api_key"),
                max_tokens=_max_tokens_for(llm, role_cfg, role),
                model_overrides=_role_overrides(llm, None, role_cfg, model),
                reasoning_effort=_effort_from(role_cfg),
                thinking=_think_from(role_cfg),
                cache_options=cache_options,
            )
    if data.get("name") or data.get("model") or data.get("base_url"):
        # Sin rol, el proveedor global es el guardado: se leen sus ajustes (tope de salida y
        # capacidades por modelo) de su propia entrada, no del nivel superior.
        entry = _active_provider_entry(data)
        return ProviderConfig(
            name=data.get("name", "fake"),
            model=data.get("model", "fake-model"),
            api_key=data.get("api_key"),
            base_url=data.get("base_url"),
            temperature=float(data.get("temperature", 0.0)),
            max_tokens=_max_tokens_for(llm, entry or data),
            model_overrides=_model_overrides(llm, entry or data),
            reasoning_effort=_effort_from(llm, data),
            thinking=_think_from(llm, entry or data, data),
            cache_options=cache_options,
            auth_type=str(entry.get("auth_type") or ""),
        )
    return None
