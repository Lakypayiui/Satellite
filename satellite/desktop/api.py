"""API local de la app de escritorio. Todo pasa por el runtime de siempre."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from satellite.desktop.backend import DesktopBackend
from satellite.desktop.events import event_stream


class MessageIn(BaseModel):
    """Un mensaje del usuario."""

    text: str = Field(min_length=1)
    plan_mode: bool = False


class PlanChooseIn(BaseModel):
    """Alternativa del modo plan que se elige."""

    option_id: str = Field(min_length=1)


class ProjectIn(BaseModel):
    """Carpeta a abrir."""

    path: str = Field(min_length=1)


class SessionIn(BaseModel):
    """Nombre de la sesión a crear (opcional)."""

    name: str = ""


class SessionPatchIn(BaseModel):
    """Cambios sobre una sesión guardada; sólo se aplica lo que llega (``None`` = igual).

    Mantiene el ``PATCH /sessions/{id}`` existente: renombrar (``name``), fijar (``pinned``)
    o archivar (``archived``). Fijar no cambia la conversación activa.
    """

    name: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ApprovalIn(BaseModel):
    """Decisión sobre una propuesta pendiente."""

    decision: str
    feedback: str = ""
    reason: str = ""
    # Proyecto que pidió la propuesta. La interfaz lo recibe en ``GET /approvals`` y lo
    # devuelve aquí; sin él, el backend sólo resuelve ids que no sean ambiguos.
    project_root: str = ""


class RestoreIn(BaseModel):
    """Confirmación explícita para descartar cambios."""

    confirm: bool = False


class ConfigIn(BaseModel):
    """Cambios de política y modo seguro."""

    policy_mode: str = ""
    safe_mode: bool | None = None


class DesktopControlPatchIn(BaseModel):
    """Activa/desactiva el control, edita la allowlist y elige visión."""

    enabled: bool | None = None
    # Apps permitidas por la política (la denylist local siempre gana).
    allowed_processes: list[str] | None = None
    vision_provider: str | None = None
    vision_model: str | None = None


class SafeProxyBindIn(BaseModel):
    """Dónde escucha el servicio embebido: host loopback y puerto válido.

    No hay campos de token: los genera Satellite en el host y son **write-only**, nunca
    entran ni salen por la interfaz.
    """

    host: str = ""
    port: int | None = None


class SafeProxyConfigIn(BaseModel):
    """Cambios allowlisted del proxy: sólo las secciones que el proxy acepta."""

    policy: dict | None = None
    code: dict | None = None
    upstream: dict | None = None


class SafeProxyModeIn(BaseModel):
    """Modo del Safe Proxy: ``internal`` (sin listener) o ``server`` (loopback)."""

    mode: str = Field(min_length=1)


class SafeProxyToggleIn(BaseModel):
    """Enciende/apaga de verdad: el interruptor interno o el routing del servidor."""

    enabled: bool


class ProviderIn(BaseModel):
    """Proveedor a guardar y activar, con sus ajustes (esfuerzo, tope de salida)."""

    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = ""
    api_key_env: str = ""
    account_id: str = ""
    auth_type: str = ""
    reasoning_effort: str = ""
    think: bool | None = None
    max_tokens: int = 0
    # Niveles de esfuerzo que anunció el endpoint para el modelo (para validar y persistir).
    effort_levels: list[str] = []
    effort_control: str = ""
    effort_can_disable: bool = False


class RoleIn(BaseModel):
    """Asignación de un rol a un proveedor guardado (con su esfuerzo, si aplica)."""

    role: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    think: bool | None = None
    effort_levels: list[str] = []
    effort_control: str = ""


class ActiveProviderIn(BaseModel):
    """Proveedor guardado que pasa a ser el global (el que resuelve el runtime)."""

    provider: str = Field(min_length=1)


class AccountProviderIn(BaseModel):
    """Modelo seleccionado para una cuenta conectada de un proveedor."""

    model: str = Field(min_length=1)


class ProviderPatchIn(BaseModel):
    """Cambios sobre un proveedor guardado; sólo se aplica lo que llega (``None`` = igual)."""

    name: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str = ""
    api_key_env: str | None = None
    account_id: str | None = None
    auth_type: str | None = None
    reasoning_effort: str | None = None
    think: bool | None = None
    max_tokens: int | None = None
    effort_levels: list[str] | None = None
    effort_control: str = ""
    effort_can_disable: bool = False


class SnapshotIn(BaseModel):
    """Snapshot a restaurar (con confirmación explícita)."""

    name: str = Field(min_length=1)
    confirm: bool = False


class ResetIn(BaseModel):
    """Reset del estado base."""

    keep_skills: bool = True
    confirm: bool = False


class CompactIn(BaseModel):
    """Compactado manual del contexto."""

    keep: int | None = None


class SkillEditIn(BaseModel):
    """Cambios de una skill existente."""

    description: str | None = None
    tags: list[str] | None = None


class CurateIn(BaseModel):
    """Petición para el curador de skills."""

    request: str = ""


class NeedIn(BaseModel):
    """Necesidad de capacidad para crear un agente."""

    need: str = Field(min_length=1)


class WaitIn(BaseModel):
    """Espera de un trabajo en segundo plano."""

    timeout: float = 30.0


class GitPathsIn(BaseModel):
    """Rutas (relativas al repo) para stage/unstage."""

    paths: list[str] = Field(min_length=1)


class GitCommitIn(BaseModel):
    """Mensaje del commit explícito."""

    message: str = Field(min_length=1)


class WorktreeIn(BaseModel):
    """Worktree a crear (nombre gestionado; rama y base opcionales)."""

    name: str = Field(min_length=1)
    branch: str = ""
    base_ref: str = ""


class WorktreeRemoveIn(BaseModel):
    """Worktree a quitar; ``force`` sólo con confirmación del usuario."""

    path: str = Field(min_length=1)
    force: bool = False


class WorktreeActiveIn(BaseModel):
    """Contexto de ejecución a activar (``""`` = el proyecto)."""

    path: str = ""


class CommentIn(BaseModel):
    """Comentario inline anclado a un rango de líneas del diff."""

    file: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int | None = None
    text: str = Field(min_length=1)
    side: str = "new"
    task_id: str = ""
    session_id: str = ""
    author: str = ""


class CommentPatchIn(BaseModel):
    """Resolver/reabrir un comentario (y opcionalmente editar su texto)."""

    text: str | None = None
    resolved: bool | None = None


class QueueIn(BaseModel):
    """Instrucción a encolar mientras corre un turno."""

    text: str = Field(min_length=1)
    plan_mode: bool = False


class QueuePatchIn(BaseModel):
    """Editar un item aún no ejecutado de la cola."""

    text: str = Field(min_length=1)
    plan_mode: bool | None = None


def build_router(backend: DesktopBackend, auth: Callable) -> APIRouter:
    """Rutas de la app; ``auth`` se aplica a todas (menos ``/api/health``)."""
    router = APIRouter(prefix="/api", dependencies=[Depends(auth)])

    # --- estado y proyectos ---

    @router.get("/status")
    def status() -> dict:
        """Estado del proyecto abierto, y si el runtime está listo y por qué no."""
        return backend.status()

    @router.get("/projects")
    def projects() -> dict:
        """Proyectos recordados."""
        return {"current": str(backend.project_root), "recents": backend.recents()}

    @router.post("/projects")
    def open_project(payload: ProjectIn) -> dict:
        """Abre o cambia de carpeta de proyecto."""
        return backend.open_project(payload.path)

    # --- sesiones ---

    @router.get("/sessions")
    def sessions() -> dict:
        """Conversaciones guardadas del proyecto, la activa primero."""
        return backend.sessions_payload()

    @router.post("/sessions")
    def new_session(payload: SessionIn | None = None) -> dict:
        return backend.new_session((payload.name if payload else "") or "")

    @router.patch("/sessions/{session_id}")
    def patch_session(session_id: str, payload: SessionPatchIn) -> dict:
        """Renombra, fija o archiva una sesión (sólo lo que llega). Fijar no cambia la activa."""
        return backend.update_session(
            session_id, name=payload.name, pinned=payload.pinned, archived=payload.archived
        )

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict:
        """Borra la conversación y sus turnos. No se puede deshacer."""
        return backend.delete_session(session_id)

    @router.post("/sessions/{session_id}/activate")
    def activate_session(session_id: str) -> dict:
        return backend.activate_session(session_id)

    @router.get("/sessions/{session_id}/turns")
    def session_turns(session_id: str) -> dict:
        return {"session_id": session_id, "turns": backend.session_turns(session_id)}

    @router.get("/diagrams")
    def diagram_svg(path: str) -> dict:
        """SVG markup for a diagram artifact (confined to the open project)."""
        return backend.diagram_svg(path)

    # --- conversación ---

    @router.post("/messages")
    async def send_message(payload: MessageIn) -> dict:
        """Un turno con el agente; devuelve la respuesta (y la actividad va por SSE)."""
        return await backend.send(payload.text, plan_mode=payload.plan_mode)

    @router.post("/turns/cancel")
    async def cancel_turn() -> dict:
        """Corta el turno en curso: cooperativa si el runtime cede, y si no, lo corta."""
        return await backend.cancel_turn()

    # --- modo plan ---

    @router.get("/plan")
    def plan() -> dict:
        """Alternativas del modo plan pendientes de elección (o ``null``)."""
        return {"plan": backend.pending_plan()}

    @router.post("/plan")
    async def choose_plan(payload: PlanChooseIn) -> dict:
        """Elige una alternativa del modo plan y continúa (igual que ``ChatSession.choose``)."""
        return await backend.choose_plan(payload.option_id)

    # --- aprobaciones ---

    @router.get("/approvals")
    def approvals() -> dict:
        """Permisos pendientes, con su motivo y las opciones disponibles."""
        return {"pending": backend.pending_approvals()}

    @router.post("/approvals/{proposal_id}")
    def resolve_approval(proposal_id: str, payload: ApprovalIn) -> dict:
        """Responde a una propuesta: permitir una vez, durante la sesión, o rechazar."""
        return backend.resolve_approval(
            proposal_id,
            payload.decision,
            feedback=payload.feedback,
            reason=payload.reason,
            project_root=payload.project_root or None,
        )

    # --- eventos en vivo ---

    @router.get("/events")
    async def events(after: int = 0) -> StreamingResponse:
        """Flujo SSE con índice, para reanudar sin repetir ni saltarse nada.

        No se sondea la desconexión en cada trozo: si el cliente se va, Starlette cierra
        el generador y el ``finally`` de ``event_stream`` limpia la suscripción; para
        reconectar está el índice ``after``, que no se salta ni repite nada.
        """
        return StreamingResponse(
            event_stream(backend.bridge(), after, stop=backend.stream_stop()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- tareas, cambios y trabajos ---

    @router.get("/tasks")
    async def tasks() -> dict:
        return await backend.tasks()

    @router.get("/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict:
        return await backend.task_detail(task_id)

    @router.get("/tasks/{task_id}/changes")
    def changes(task_id: str) -> dict:
        return backend.changes(task_id)

    @router.get("/tasks/{task_id}/diff")
    def diff(task_id: str) -> dict:
        return {"task_id": task_id, "diff": backend.changes(task_id)["diff"]}

    @router.post("/tasks/{task_id}/restore")
    def restore(task_id: str, payload: RestoreIn | None = None) -> dict:
        """Sin confirmación no toca nada: devuelve lo que se descartaría."""
        return backend.restore(task_id, confirm=bool(payload and payload.confirm))

    @router.get("/jobs")
    def jobs() -> dict:
        return {"jobs": backend.jobs()}

    @router.get("/jobs/{job_id}/output")
    def job_output(job_id: str) -> dict:
        return backend.job_output(job_id)

    @router.post("/jobs/{job_id}/stop")
    def job_stop(job_id: str) -> dict:
        return backend.job_stop(job_id)

    @router.post("/jobs/{job_id}/wait")
    async def job_wait(job_id: str, payload: WaitIn | None = None) -> dict:
        """Espera a que termine un trabajo (como ``/jobs wait``), sin bloquear la app."""
        return await backend.job_wait(job_id, timeout_s=(payload.timeout if payload else 30.0))

    # --- git (estado y operaciones explícitas) y worktrees ---

    @router.get("/git/status")
    def git_status() -> dict:
        """Estado real del repo: rama, cambios, staged/unstaged y conflictos."""
        return backend.git_status()

    @router.post("/git/stage")
    def git_stage(payload: GitPathsIn) -> dict:
        return backend.git_stage(payload.paths)

    @router.post("/git/unstage")
    def git_unstage(payload: GitPathsIn) -> dict:
        return backend.git_unstage(payload.paths)

    @router.post("/git/commit")
    def git_commit(payload: GitCommitIn) -> dict:
        """Commit explícito (nunca hace push ni toca remotos)."""
        return backend.git_commit(payload.message)

    @router.get("/worktrees")
    def worktrees() -> dict:
        """Worktrees del repo y cuál es el contexto de ejecución activo."""
        return backend.worktrees()

    @router.post("/worktrees")
    def worktree_add(payload: WorktreeIn) -> dict:
        return backend.add_worktree(payload.name, branch=payload.branch, base_ref=payload.base_ref)

    @router.post("/worktrees/active")
    def worktree_activate(payload: WorktreeActiveIn) -> dict:
        """Cambia el contexto de ejecución (worktree) y rearma el runtime."""
        return backend.set_worktree(payload.path)

    @router.post("/worktrees/remove")
    def worktree_remove(payload: WorktreeRemoveIn) -> dict:
        """Quita un worktree gestionado; sin ``force`` se rechaza si tiene cambios."""
        return backend.remove_worktree(payload.path, force=payload.force)

    # --- comentarios inline del diff ---

    @router.get("/comments")
    def comments(
        file: str = "",
        task_id: str = "",
        session_id: str = "",
        include_resolved: bool = True,
    ) -> dict:
        """Comentarios persistidos, filtrables por archivo/tarea/sesión."""
        return backend.comments(
            file=file or None,
            task_id=task_id or None,
            session_id=session_id or None,
            include_resolved=include_resolved,
        )

    @router.post("/comments")
    def add_comment(payload: CommentIn) -> dict:
        return backend.add_comment(
            file=payload.file,
            line_start=payload.line_start,
            line_end=payload.line_end,
            text=payload.text,
            side=payload.side,
            task_id=payload.task_id,
            session_id=payload.session_id,
            author=payload.author,
        )

    @router.patch("/comments/{comment_id}")
    def update_comment(comment_id: str, payload: CommentPatchIn) -> dict:
        return backend.update_comment(
            comment_id, text=payload.text, resolved=payload.resolved
        )

    @router.delete("/comments/{comment_id}")
    def delete_comment(comment_id: str) -> dict:
        return backend.delete_comment(comment_id)

    # --- cola de instrucciones (persistida) ---

    @router.get("/queue")
    def queue() -> dict:
        """Instrucciones encoladas (la primera es la siguiente a ejecutar)."""
        return backend.queue()

    @router.post("/queue")
    def enqueue(payload: QueueIn) -> dict:
        """Encola el siguiente prompt mientras corre un turno."""
        return backend.enqueue(payload.text, plan_mode=payload.plan_mode)

    @router.post("/queue/clear")
    def clear_queue() -> dict:
        return backend.clear_queue()

    @router.patch("/queue/{item_id}")
    def update_queue(item_id: str, payload: QueuePatchIn) -> dict:
        return backend.update_queue_item(item_id, text=payload.text, plan_mode=payload.plan_mode)

    @router.delete("/queue/{item_id}")
    def remove_queue(item_id: str) -> dict:
        return backend.remove_queue_item(item_id)

    # --- catálogos y configuración ---

    @router.get("/agents")
    def agents() -> dict:
        return {"agents": backend.agents()}

    @router.get("/skills")
    def skills() -> dict:
        return {"skills": backend.skills()}

    @router.get("/config")
    def config() -> dict:
        """Configuración y política, con los proveedores redactados."""
        return backend.config()

    @router.put("/config")
    def update_config(payload: ConfigIn) -> dict:
        return backend.update_config(
            policy_mode=payload.policy_mode, safe_mode=payload.safe_mode
        )

    @router.get("/desktop-control")
    def desktop_control() -> dict:
        """Control de escritorio: flag local, visión seleccionada y límites."""
        return backend.desktop_control()

    @router.patch("/desktop-control")
    def update_desktop_control(payload: DesktopControlPatchIn) -> dict:
        """Sólo configuración local; no concede permisos ni se expone por MCP."""
        return backend.update_desktop_control(
            enabled=payload.enabled,
            allowed_processes=payload.allowed_processes,
            vision_provider=payload.vision_provider,
            vision_model=payload.vision_model,
        )

    # --- Safe Proxy embebido: ciclo de vida y configuración (sólo loopback) ---

    @router.get("/safe-proxy")
    def safe_proxy() -> dict:
        """Estado real (stopped/running/config-error/port-conflict) y config redactada."""
        return backend.safe_proxy()

    @router.post("/safe-proxy/mode")
    def set_safe_proxy_mode(payload: SafeProxyModeIn) -> dict:
        """Cambia de modo: interno (sin listener) o servidor (loopback, con start/stop)."""
        return backend.set_safe_proxy_mode(payload.mode)

    @router.post("/safe-proxy/start")
    def start_safe_proxy() -> dict:
        """Arranca el servicio embebido en loopback (in-process, sin procesos externos)."""
        return backend.start_safe_proxy()

    @router.post("/safe-proxy/stop")
    def stop_safe_proxy() -> dict:
        """Para el servicio embebido y libera el puerto."""
        return backend.stop_safe_proxy()

    @router.post("/safe-proxy/connection")
    def configure_safe_proxy(payload: SafeProxyBindIn) -> dict:
        """Cambia host/puerto del servicio embebido (loopback + puerto válido)."""
        return backend.configure_safe_proxy(host=payload.host or None, port=payload.port)

    @router.patch("/safe-proxy/config")
    def patch_safe_proxy_config(payload: SafeProxyConfigIn) -> dict:
        """Aplica la allowlist en el servicio vivo; el servicio valida y persiste."""
        changes = {
            key: value
            for key, value in (
                ("policy", payload.policy),
                ("code", payload.code),
                ("upstream", payload.upstream),
            )
            if value is not None
        }
        return backend.patch_safe_proxy_config(changes)

    @router.post("/safe-proxy/toggle")
    def toggle_safe_proxy(payload: SafeProxyToggleIn) -> dict:
        """Habilita/deshabilita el routing real del servicio (enable/disable)."""
        return backend.set_safe_proxy_enabled(payload.enabled)

    @router.get("/providers")
    def providers() -> dict:
        return {"providers": backend.config()["providers"]}

    @router.get("/providers/chatgpt-models")
    def chatgpt_models() -> dict:
        return backend.chatgpt_account_models()

    @router.post("/providers/chatgpt-account")
    def save_chatgpt_account_provider(payload: AccountProviderIn) -> dict:
        return backend.save_chatgpt_account_provider(payload.model)

    @router.get("/providers/claude-models")
    def claude_models() -> dict:
        return backend.claude_account_models()

    @router.post("/providers/claude-account")
    def save_claude_account_provider(payload: AccountProviderIn) -> dict:
        return backend.save_claude_account_provider(payload.model)

    @router.post("/providers")
    def save_provider(payload: ProviderIn) -> dict:
        """Guarda y activa un proveedor; la clave nunca vuelve en la respuesta."""
        return backend.save_provider(
            name=payload.name,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key,
            api_key_env=payload.api_key_env,
            account_id=payload.account_id,
            auth_type=payload.auth_type,
            reasoning_effort=payload.reasoning_effort,
            think=payload.think,
            max_tokens=payload.max_tokens,
            effort_levels=payload.effort_levels,
            effort_control=payload.effort_control,
            effort_can_disable=payload.effort_can_disable,
        )

    @router.delete("/providers/{provider_id}")
    def remove_provider(provider_id: str) -> dict:
        """Elimina un proveedor guardado; los demás no se tocan."""
        return backend.remove_provider(provider_id)

    @router.post("/providers/active")
    def set_active_provider(payload: ActiveProviderIn) -> dict:
        """Deja un proveedor guardado como global (lo que usa el runtime si el rol no manda)."""
        return backend.set_active_provider(payload.provider)

    @router.patch("/providers/{provider_id}")
    def patch_provider(provider_id: str, payload: ProviderPatchIn) -> dict:
        """Actualiza un proveedor guardado (esfuerzo, tope de salida, modelo, credenciales…)."""
        return backend.patch_provider(
            provider_id,
            name=payload.name,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key,
            api_key_env=payload.api_key_env,
            account_id=payload.account_id,
            auth_type=payload.auth_type,
            reasoning_effort=payload.reasoning_effort,
            think=payload.think,
            max_tokens=payload.max_tokens,
            effort_levels=payload.effort_levels,
            effort_control=payload.effort_control,
            effort_can_disable=payload.effort_can_disable,
        )

    @router.get("/models")
    def models(
        base_url: str = "",
        family: str = "openai",
        api_key: str = "",
        api_key_env: str = "",
        provider: str = "",
    ) -> dict:
        """Modelos de esa URL, con el más usado sugerido (como la TUI).

        Con `provider` (id guardado) usa sus credenciales del servidor: la clave no sale.
        """
        return backend.models(
            base_url=base_url,
            family=family,
            api_key=api_key,
            api_key_env=api_key_env,
            provider=provider,
        )

    @router.post("/shutdown")
    async def shutdown() -> dict:
        """Cierre pedido por la app: drena lo pendiente antes de que la maten."""
        await backend.close()
        return {"closing": True}

    # --- catálogo, roles y mantenimiento: todo lo que ya hace la TUI ---

    @router.get("/providers/catalog")
    def provider_catalog() -> dict:
        """Catálogo con la URL ya puesta, como el asistente del CLI."""
        return {"catalog": backend.provider_catalog()}

    @router.get("/roles")
    def roles() -> dict:
        """Qué proveedor usa cada rol (orchestrator, context, agents, creator)."""
        return backend.roles()

    @router.put("/roles")
    def apply_role(payload: RoleIn) -> dict:
        """Fija un rol a un proveedor guardado: sólo ese rol, sin cascada."""
        return backend.apply_role(
            payload.role,
            payload.provider,
            payload.model,
            reasoning_effort=payload.reasoning_effort,
            think=payload.think,
            effort_levels=payload.effort_levels,
            effort_control=payload.effort_control,
        )

    @router.get("/doctor")
    def doctor() -> dict:
        return {"checks": backend.doctor()}

    @router.post("/setup")
    def setup_sandbox() -> dict:
        return backend.setup_sandbox()

    @router.get("/stats")
    def stats() -> dict:
        return backend.stats()

    @router.get("/commands")
    def commands() -> dict:
        """Catálogo compartido completo y su informe de paridad (una definición, tres interfaces)."""
        return backend.commands()

    @router.get("/tools")
    def tools() -> dict:
        """Herramientas del runtime, marcando las primitivas del kernel."""
        return {"tools": backend.tools()}

    @router.get("/context")
    def context() -> dict:
        """Uso de contexto detallado de la conversación activa."""
        return backend.context_usage()

    @router.get("/context/mentions")
    def context_mentions(q: str = "", limit: int = 20) -> dict:
        """Candidatos para una @mención del compositor (rutas del proyecto)."""
        return {"suggestions": backend.mention_suggestions(q, limit=max(1, min(limit, 100)))}

    @router.post("/init")
    def init_project() -> dict:
        """Inicializa o repara el proyecto (crea .satellite/ y migra el esquema)."""
        return backend.initialize()

    @router.get("/snapshots")
    def snapshots() -> dict:
        return backend.snapshots()

    @router.post("/restore")
    def restore_snapshot(payload: SnapshotIn) -> dict:
        """Sin confirmación no restaura nada: dice qué se descartaría."""
        return backend.restore_snapshot(payload.name, confirm=payload.confirm)

    @router.post("/reset")
    def reset_project(payload: ResetIn | None = None) -> dict:
        """Sin confirmación no toca nada: borra agentes y skills creados."""
        return backend.reset_project(
            keep_skills=bool(payload and payload.keep_skills),
            confirm=bool(payload and payload.confirm),
        )

    @router.post("/compact")
    async def compact(payload: CompactIn | None = None) -> dict:
        return await backend.compact(keep=payload.keep if payload else None)

    @router.get("/skills/search")
    def skill_search(q: str = "") -> dict:
        return {"results": backend.skill_search(q)}

    @router.patch("/skills/{skill_id}")
    def skill_edit(skill_id: str, payload: SkillEditIn) -> dict:
        return backend.skill_edit(
            skill_id, description=payload.description, tags=payload.tags
        )

    @router.delete("/skills/{skill_id}")
    def skill_delete(skill_id: str) -> dict:
        return backend.skill_delete(skill_id)

    @router.post("/curate")
    async def curate(payload: CurateIn | None = None) -> dict:
        """Ejecuta el curador de skills (puede pedir aprobaciones, y se muestran)."""
        return await backend.curate(payload.request if payload else "")

    @router.post("/agents")
    async def create_agent(payload: NeedIn) -> dict:
        """Crea un agente para cubrir una necesidad (con aprobación, no a ciegas)."""
        return await backend.create_agent(payload.need)

    return router
