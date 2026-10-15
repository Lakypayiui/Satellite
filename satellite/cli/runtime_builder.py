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
from satellite.cli.console import say
from satellite.config import providers as provider_config
from satellite.core.errors import ProviderError
from satellite.core.events import EventType, ExecutionEvent
from satellite.events.bus import EventBus
from satellite.kernel.manifest import RESERVED_AGENT_IDS, verify_and_restore
from satellite.kernel.migrations import migrate, migrate_agent_specs, migrate_prompt_files
from satellite.kernel.paths import ensure_dirs, resolve_paths
from satellite.kernel.policy import PolicyMode, Settings, load_settings
from satellite.metrics.pricing import PriceBook
from satellite.observability.call_log import CallRecorder, default_log_dir
from satellite.observability.logger import ExecutionLogger
from satellite.providers.metered import MeteredProvider
from satellite.skills.cache import SkillResultCache
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.delegation import DelegationManager
from satellite.orchestration.orchestrator import DefaultOrchestrator
from satellite.orchestration.plan_mode import PlanOptionGenerator
from satellite.orchestration.planner import LLMPlanner
from satellite.orchestration.runner import DefaultGraphRunner
from satellite.persistence.store import FileStore
from satellite.providers.base import LLMProvider
from satellite.providers.factory import ProviderConfig, ProviderFactory
from satellite.sandbox.subprocess_sandbox import SubprocessSandbox
from satellite.security.policy import DefaultSecurityPolicy
from satellite.skills.registry import SkillRegistry
from satellite.skills.runner import SkillRunner
from satellite.tools.context_tool import RequestContextTool
from satellite.tools.delegate import DelegateTool
from satellite.tools.fs import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from satellite.tools.git import GitStatusTool
from satellite.tools.primitives import RunPythonTool, RunShellTool, SearchFilesTool
from satellite.tools.registry import DefaultToolRegistry
from satellite.tools.skills import ProposeSkillTool, SearchSkillsTool
from satellite.tools.terminal import RunCommandTool

DEFAULT_ALLOWED_TOOLS: set[str] = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "request_context",
    "run_python",
    "run_shell",
    "run_command",
    "search_files",
    "git_status",
    "search_skills",
    "propose_skill",
    "delegate",
}

AGENTS_SUBDIR = Path("agents")
CONFIG_PATH = Path(".satellite") / "config" / "config.json"

_SAFE_MODE = False


def set_safe_mode(enabled: bool) -> None:
    """Activa/desactiva el modo seguro (sólo agentes base y primitivas)."""
    global _SAFE_MODE
    _SAFE_MODE = bool(enabled)


def build_runtime(project_root: Path, *, approval: object | None = None):
    """Construye (orchestrator, bus, store, registry) para un project root."""
    project_root = Path(project_root).resolve()
    base_dir = Path(__file__).parent.parent / "agents"
    paths = ensure_dirs(resolve_paths(project_root))
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

    # Seguridad + tools (deny-by-default).
    policy = DefaultSecurityPolicy(allowed_tools=set(DEFAULT_ALLOWED_TOOLS))
    tools = build_tool_registry(policy)

    bus = EventBus()
    provider = _metered(_load_provider(project_root), project_root=project_root, settings=settings)

    # Skills globales (carga tolerante) + sandbox N0.
    skill_registry = SkillRegistry(paths.skills_root, sink=None)
    skill_registry.load_all()
    sandbox = SubprocessSandbox()
    skill_runner = SkillRunner(
        skill_registry,
        sandbox,
        cache=SkillResultCache(_skill_cache_path(paths)),
    )

    # Catálogo: base (ya sincronizado) + agentes creados por el usuario.
    loaded_specs = {spec.id: spec for spec in catalog_store.load_specs()}
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

    factory = DefaultAgentFactory(
        registry=registry,
        prompts=prompt_reg,
        tools=tools,
        provider=provider,
        policy=policy,
        context_engine=context,
        project_root=project_root,
    )
    delegation = DelegationManager(
        factory,
        registry=registry,
        sink=bus,
        approval=approval_port,
    )
    factory.delegation_manager = delegation

    # Creator + validador + expander (rol de modelo propio por config).
    creator_provider = _metered(
        _role_provider(project_root, "creator", provider),
        project_root=project_root,
        settings=settings,
        role="creator",
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
    services = RuntimeServices(
        registry=registry,
        factory=factory,
        tools=tools,
        context=context,
        provider=provider,
        sink=bus,
        approval=approval_port,
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
    )
    orchestrator.preprocessor = _build_preprocessor(project_root, provider, settings)
    orchestrator.skill_runner = skill_runner
    return orchestrator, bus, store, registry


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


def build_tool_registry(policy: DefaultSecurityPolicy | None = None) -> DefaultToolRegistry:
    """Registro de herramientas del runtime (primitivas del kernel incluidas).

    Se expone aparte para poder listarlas desde el CLI sin levantar el runtime
    completo (y por tanto sin necesitar un proveedor configurado).
    """
    if policy is None:
        policy = DefaultSecurityPolicy(allowed_tools=set(DEFAULT_ALLOWED_TOOLS))
    tools = DefaultToolRegistry(policy=policy)
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirTool(),
        RunCommandTool(),
        RunShellTool(),
        RunPythonTool(),
        SearchFilesTool(),
        GitStatusTool(),
        RequestContextTool(),
        DelegateTool(),
        SearchSkillsTool(),
        ProposeSkillTool(),
    ):
        tools.register(tool)
    return tools


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
    if isinstance(provider, MeteredProvider):
        return provider
    data = provider_config.load_config(project_root)
    cfg = _provider_config_from(data, role=role) or _provider_config_from(data)
    return MeteredProvider(
        provider,
        CallRecorder(default_log_dir(project_root)),
        provider_name=(cfg.name if cfg is not None else ""),
        model=(cfg.model if cfg is not None else ""),
        price_book=PriceBook.from_config(data),
        max_tokens_by_role=settings.max_tokens,
    )


def _load_provider(project_root: Path) -> LLMProvider:
    """Carga el proveedor global desde ``.satellite/config/config.json``.

    Precedencia: rol ``llm.orchestrator`` → top-level. Los providers guardados
    se resuelven por id.
    """
    config_path = project_root / CONFIG_PATH
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            cfg = _provider_config_from(data, role="orchestrator") or _provider_config_from(data)
            if cfg is None:
                raise ProviderError("config sin proveedor")
            return ProviderFactory.create(cfg)
        except Exception as exc:
            raise ProviderError(
                f"config de proveedor inválida en {config_path}: {exc}. "
                "Configura un proveedor válido en 'satellite config'."
            ) from exc
    raise ProviderError(
        f"no hay proveedor LLM configurado: falta {config_path}. "
        "Configura un proveedor (p. ej. {'name': 'openai', 'model': '...', "
        "'api_key': '...'}) o crea el archivo config.json para ejecutar tareas."
    )


def _role_provider(project_root: Path, role: str, default: LLMProvider) -> LLMProvider:
    """Proveedor del rol indicado; cae al global si no está configurado."""
    config_path = project_root / CONFIG_PATH
    if not config_path.exists():
        return default
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
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
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _model_overrides(llm: dict, provider: dict | None = None) -> dict:
    """Capacidades por modelo: ``llm.models`` + las del proveedor guardado."""
    result: dict = {}
    for source in (llm.get("models"), (provider or {}).get("models")):
        if isinstance(source, dict):
            result.update(source)
    return result


def _provider_config_from(data: dict, role: str | None = None) -> ProviderConfig | None:
    """Construye ProviderConfig desde el config, priorizando el rol indicado."""
    llm = data.get("llm", {}) if isinstance(data.get("llm"), dict) else {}
    if role and isinstance(llm.get(role), dict):
        role_cfg = llm[role]
        ref = str(role_cfg.get("provider", ""))
        saved = data.get("providers", []) if isinstance(data.get("providers"), list) else []
        saved_match = next(
            (p for p in saved if isinstance(p, dict) and p.get("id") == ref), None
        )
        if saved_match is not None:
            return ProviderConfig(
                name=saved_match.get("name", "openai"),
                model=role_cfg.get("model") or saved_match.get("model", ""),
                base_url=saved_match.get("base_url", ""),
                api_key=saved_match.get("api_key"),
                max_tokens=_max_tokens_for(llm, saved_match, role),
                model_overrides=_model_overrides(llm, saved_match),
            )
        if role_cfg.get("base_url") or role_cfg.get("model") or role_cfg.get("provider"):
            return ProviderConfig(
                name=role_cfg.get("provider", "openai"),
                model=role_cfg.get("model", "gpt-4o-mini"),
                base_url=role_cfg.get("base_url"),
                api_key=role_cfg.get("api_key"),
                max_tokens=_max_tokens_for(llm, role_cfg, role),
                model_overrides=_model_overrides(llm, role_cfg),
            )
    if data.get("name") or data.get("model") or data.get("base_url"):
        return ProviderConfig(
            name=data.get("name", "fake"),
            model=data.get("model", "fake-model"),
            api_key=data.get("api_key"),
            base_url=data.get("base_url"),
            temperature=float(data.get("temperature", 0.0)),
            max_tokens=_max_tokens_for(llm, data),
            model_overrides=_model_overrides(llm, data),
        )
    return None
