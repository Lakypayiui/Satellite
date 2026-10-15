"""Sesión de trabajo: estado y operaciones del framework, sin dependencias de UI.

La TUI (Textual) y los tests usan esta capa; toda la lógica vive aquí para
poder probarla sin terminal.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from satellite.agents.curator import CuratorOutcome, SkillCurator
from satellite.agents.expander import ExpansionOutcome
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.store import AgentCatalogStore
from satellite.approval.base import ApprovalPort
from satellite.cli import runtime_builder
from satellite.config import catalog as provider_catalog
from satellite.config import providers as provider_config
from satellite.config.models import ModelsResult, fetch_models
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult
from satellite.kernel import migrations
from satellite.kernel.manifest import verify_and_restore
from satellite.kernel.paths import ensure_dirs, resolve_paths
from satellite.kernel.policy import load_settings
from satellite.metrics.stats import MetricsReport, build_report
from satellite.orchestration.chat import ChatReply, ChatSession, Turn
from satellite.persistence.store import FileStore
from satellite.sandbox.setup import detect_docker, record_decision
from satellite.skills.models import SkillManifest
from satellite.skills.registry import SkillRegistry

CONFIG_PATH = Path(".satellite") / "config" / "config.json"
PACKAGE_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


@dataclass
class SessionStatus:
    """Resumen del estado de la sesión (para la barra superior)."""

    project_root: str = ""
    initialized: bool = False
    agents: int = 0
    skills: int = 0
    provider: str = ""
    model: str = ""
    policy_mode: str = "ask"
    sandbox: str = "N0"
    safe_mode: bool = False


class Session:
    """Expone todas las funcionalidades del framework a la TUI."""

    def __init__(self, project_root: Path | None = None, *, home: Path | None = None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self._home = Path(home) if home is not None else None
        self._runtime: tuple | None = None
        self._chat: ChatSession | None = None

    # --- rutas y configuración ---

    def _env(self) -> dict | None:
        return {"SATELLITE_HOME": str(self._home)} if self._home is not None else None

    def paths(self):
        """Rutas de datos (globales + proyecto), creando los directorios."""
        return ensure_dirs(resolve_paths(self.project_root, env=self._env()))

    def settings(self):
        """Configuración del kernel (política, cuotas, sandbox)."""
        return load_settings(self.project_root / CONFIG_PATH)

    def config_data(self) -> dict:
        """Contenido de config.json (vacío si falta o es inválido)."""
        path = self.project_root / CONFIG_PATH
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def write_config(self, data: dict) -> None:
        """Escribe config.json (creando el directorio)."""
        path = self.project_root / CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._runtime = None

    # --- proveedor (configuración compartida con el CLI) ---

    def provider_catalog(self):
        """Catálogo de proveedores conocidos (con su URL base)."""
        return provider_catalog.CATALOG

    def saved_providers(self):
        """Proveedores guardados (nunca con la api_key)."""
        return provider_config.list_providers(self.project_root)

    def active_provider_id(self) -> str:
        """Id del proveedor global activo."""
        return provider_config.active_id(self.project_root)

    def load_models(self, base_url: str, family: str, api_key: str = "") -> ModelsResult:
        """Lista los modelos de esa URL (fallo ruidoso: nunca lanza)."""
        return fetch_models(base_url, family, api_key or None)

    def save_provider(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str = "",
        api_key_env: str = "",
    ):
        """Guarda el proveedor, lo deja activo y fuerza a reconstruir el runtime."""
        previous = provider_config.active_id(self.project_root)
        saved = provider_config.save_provider(
            self.project_root,
            name=name,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_env,
        )
        report = provider_config.activate(self.project_root, saved.id, previous_id=previous)
        self._runtime = None
        self._chat = None
        return saved, report

    # --- runtime ---

    def runtime(self, *, force: bool = False) -> tuple:
        """Construye (o reutiliza) el runtime completo."""
        if self._runtime is None or force:
            self._runtime = runtime_builder.build_runtime(self.project_root)
        return self._runtime

    def bus(self):
        """Bus de eventos del runtime (lo construye si hace falta)."""
        return self.runtime()[1]

    # --- consultas ---

    def agents(self) -> list[AgentSpec]:
        """Catálogo (base sincronizado + creados) sin necesitar proveedor."""
        paths = self.paths()
        verify_and_restore(PACKAGE_AGENTS_DIR / "specs", paths.agents_root, patterns=("*.json",))
        return AgentCatalogStore(paths.agents_root).load_specs()

    def skills(self) -> list[SkillManifest]:
        """Skills globales cargadas."""
        return self.skill_registry().all()

    def skill_registry(self) -> SkillRegistry:
        """Registro de skills globales."""
        registry = SkillRegistry(self.paths().skills_root)
        registry.load_all()
        return registry

    def tasks(self) -> list[str]:
        """Ids de tareas persistidas."""
        return FileStore(self.project_root).list_tasks()

    def snapshots(self) -> list[str]:
        """Snapshots disponibles."""
        return [path.name for path in FileStore(self.project_root).list_snapshots()]

    def stats(self) -> MetricsReport:
        """Informe de métricas de skills (con el coste real por uso y la cache)."""
        from satellite.metrics.cache_report import load_cache_report

        cache = load_cache_report(self.project_root)
        return build_report(
            self.skills(),
            call_cost_tokens=int(cache.real_tokens_per_call or 0),
            cache=cache.to_dict(),
        )

    def cache_report(self):
        """Informe de cache LLM del proyecto (``calls.jsonl``)."""
        from satellite.metrics.cache_report import load_cache_report

        return load_cache_report(self.project_root)

    def status(self) -> SessionStatus:
        """Estado resumido para la barra superior."""
        config = self.config_data()
        settings = self.settings()
        return SessionStatus(
            project_root=str(self.project_root),
            initialized=(self.project_root / ".satellite").exists(),
            agents=len(self.agents()),
            skills=len(self.skills()),
            provider=str(config.get("name", "")),
            model=str(config.get("model", "")),
            policy_mode=settings.policy_mode.value,
            sandbox=settings.sandbox_level,
            safe_mode=bool(runtime_builder._SAFE_MODE),
        )

    def doctor(self) -> list[tuple[bool, str]]:
        """Chequeos de integridad (ok, mensaje)."""
        checks: list[tuple[bool, str]] = []
        config_path = self.project_root / CONFIG_PATH
        checks.append(
            (
                config_path.exists(),
                "config: " + ("presente" if config_path.exists() else "falta .satellite/config/config.json"),
            )
        )
        try:
            _orch, _bus, _store, registry = self.runtime()
            checks.append((True, f"catálogo: {len(registry.all())} agentes"))
            if getattr(registry, "errors", None):
                checks.append((False, f"catálogo: {len(registry.errors)} specs inválidos"))
        except Exception as exc:  # noqa: BLE001
            checks.append((False, f"runtime: {exc}"))
        paths = self.paths()
        version = migrations.current_version(paths.project_data)
        checks.append((version >= migrations.SCHEMA_VERSION, f"esquema: v{version}"))
        quarantines = list(paths.agents_root.glob("_quarantine/*")) + list(
            paths.skills_root.glob("_quarantine/*")
        )
        checks.append((True, f"cuarentenas: {len(quarantines)}"))
        docker = detect_docker()
        suffix = f" | docker {docker.version}" if docker.available else " | docker no disponible"
        checks.append((True, f"sandbox: N0 por defecto{suffix}"))
        return checks

    # --- acciones ---

    async def run_goal(self, goal: str, *, approval: ApprovalPort | None = None) -> TaskResult:
        """Ejecuta un objetivo con el orquestador."""
        orchestrator, _bus, _store, _registry = self.runtime()
        services = getattr(orchestrator, "services", None)
        if services is not None and approval is not None:
            services.approval = approval
        return await orchestrator.execute(Task(goal=goal, project_root=self.project_root))

    # --- conversación ---

    def chat(self) -> ChatSession:
        """Sesión de conversación (se recrea si el runtime cambia)."""
        orchestrator = self.runtime()[0]
        if self._chat is None or self._chat.orchestrator is not orchestrator:
            self._chat = ChatSession(self.project_root, orchestrator)
        return self._chat

    async def send_message(
        self,
        text: str,
        *,
        approval: ApprovalPort | None = None,
        plan_mode: bool = False,
        extra_metadata: dict | None = None,
    ) -> ChatReply:
        """Envía un mensaje al agente (conversación multi-turno)."""
        return await self.chat().send(
            text,
            approval=approval,
            plan_mode=plan_mode,
            extra_metadata=extra_metadata,
        )

    async def choose_option(self, option, *, approval: ApprovalPort | None = None) -> ChatReply:
        """Elige una alternativa del modo plan y continúa."""
        return await self.chat().choose(option, approval=approval)

    def new_conversation(self) -> None:
        """Olvida la conversación actual."""
        if self._chat is not None:
            self._chat.reset()

    def conversation(self) -> tuple[Turn, ...]:
        """Turnos de la conversación actual."""
        return self._chat.recent() if self._chat is not None else ()

    async def create_agent(
        self, need: str, *, approval: ApprovalPort | None = None
    ) -> ExpansionOutcome:
        """Crea un agente para cubrir una necesidad de capacidad."""
        orchestrator, _bus, _store, _registry = self.runtime()
        expander = getattr(orchestrator, "expander", None)
        if expander is None:
            raise RuntimeError("el runtime no tiene expander (no se pueden crear agentes)")
        services = getattr(orchestrator, "services", None)
        if services is not None and approval is not None:
            services.approval = approval
        return await expander.expand(need, approval_port=approval)

    async def curate(
        self, request: str = "", *, approval: ApprovalPort | None = None
    ) -> CuratorOutcome:
        """Ejecuta el curador de skills (deduplica/edita/deprecia)."""
        orchestrator, _bus, _store, _registry = self.runtime()
        services = getattr(orchestrator, "services", None)
        prompts = DefaultPromptRegistry()
        prompts.load_dir(PACKAGE_AGENTS_DIR / "prompts")
        curator = SkillCurator(
            services.provider,
            self.skill_registry(),
            prompts,
            sink=getattr(services, "sink", None),
        )
        return await curator.run(request, approval_port=approval)

    def skill_search(self, query: str):
        """Busca skills por descripción (BM25)."""
        return self.skill_registry().find_similar(query)

    def skill_edit(
        self, skill_id: str, *, description: str | None = None, tags: list[str] | None = None
    ) -> SkillManifest:
        """Edita descripción/tags de una skill."""
        registry = self.skill_registry()
        data = registry.get(skill_id).to_dict()
        if description:
            data["description"] = description
        if tags:
            data["tags"] = list(tags)
        manifest = SkillManifest.from_dict(data)
        registry.replace_manifest(manifest)
        return manifest

    def skill_delete(self, skill_id: str) -> None:
        """Elimina una skill."""
        self.skill_registry().remove(skill_id)

    def setup_sandbox(self) -> str:
        """Detecta Docker y guarda la decisión (una sola vez)."""
        paths = self.paths()
        status = detect_docker()
        record_decision(paths.global_root, enabled=status.available)
        if status.available:
            return f"docker disponible: {status.version} (sandbox N1 listo)"
        return status.instructions

    def set_mode(self, mode: str) -> str:
        """Cambia la política de aprobación (ask/auto/unrestricted)."""
        data = self.config_data()
        data["policy_mode"] = mode
        self.write_config(data)
        return f"política de aprobación: {mode}"

    def set_safe_mode(self, enabled: bool) -> str:
        """Activa/desactiva el modo seguro."""
        runtime_builder.set_safe_mode(enabled)
        self._runtime = None
        return f"safe-mode: {'on' if enabled else 'off'}"

    def reset(self, *, keep_skills: bool = True) -> str:
        """Limpia los agentes creados (y las skills si ``keep_skills=False``)."""
        paths = self.paths()
        _clear_dir(paths.agents_root)
        if not keep_skills:
            _clear_dir(paths.skills_root)
        self._runtime = None
        return "reset: agentes limpiados" + ("" if keep_skills else " y skills eliminadas")

    def restore(self, name: str) -> str:
        """Restaura el directorio de datos desde un snapshot."""
        FileStore(self.project_root).restore(name)
        self._runtime = None
        return f"restaurado: {name}"


def _clear_dir(root: Path) -> None:
    if not root.exists():
        return
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink()
