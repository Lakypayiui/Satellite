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


class ProjectIn(BaseModel):
    """Carpeta a abrir."""

    path: str = Field(min_length=1)


class SessionIn(BaseModel):
    """Nombre de la sesión a crear (opcional)."""

    name: str = ""


class RenameIn(BaseModel):
    """Nombre nuevo de una sesión."""

    name: str = Field(min_length=1)


class ApprovalIn(BaseModel):
    """Decisión sobre una propuesta pendiente."""

    decision: str
    feedback: str = ""
    reason: str = ""


class RestoreIn(BaseModel):
    """Confirmación explícita para descartar cambios."""

    confirm: bool = False


class ConfigIn(BaseModel):
    """Cambios de política y modo seguro."""

    policy_mode: str = ""
    safe_mode: bool | None = None


class ProviderIn(BaseModel):
    """Proveedor a guardar y activar."""

    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = ""
    api_key_env: str = ""


class RoleIn(BaseModel):
    """Asignación de un rol a un proveedor guardado."""

    role: str = Field(min_length=1)
    provider: str = ""
    model: str = ""


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
    def rename_session(session_id: str, payload: RenameIn) -> dict:
        return backend.rename_session(session_id, payload.name)

    @router.post("/sessions/{session_id}/activate")
    def activate_session(session_id: str) -> dict:
        return backend.activate_session(session_id)

    @router.get("/sessions/{session_id}/turns")
    def session_turns(session_id: str) -> dict:
        return {"session_id": session_id, "turns": backend.session_turns(session_id)}

    # --- conversación ---

    @router.post("/messages")
    async def send_message(payload: MessageIn) -> dict:
        """Un turno con el agente; devuelve la respuesta (y la actividad va por SSE)."""
        return await backend.send(payload.text, plan_mode=payload.plan_mode)

    @router.post("/turns/cancel")
    def cancel_turn() -> dict:
        """Corta el turno en curso. No deshace lo ya hecho, y lo dice."""
        return backend.cancel_turn()

    # --- aprobaciones ---

    @router.get("/approvals")
    def approvals() -> dict:
        """Permisos pendientes, con su motivo y las opciones disponibles."""
        return {"pending": backend.pending_approvals()}

    @router.post("/approvals/{proposal_id}")
    def resolve_approval(proposal_id: str, payload: ApprovalIn) -> dict:
        """Responde a una propuesta: permitir una vez, durante la sesión, o rechazar."""
        return backend.resolve_approval(
            proposal_id, payload.decision, feedback=payload.feedback, reason=payload.reason
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

    @router.get("/providers")
    def providers() -> dict:
        return {"providers": backend.config()["providers"]}

    @router.post("/providers")
    def save_provider(payload: ProviderIn) -> dict:
        """Guarda y activa un proveedor; la clave nunca vuelve en la respuesta."""
        return backend.save_provider(
            name=payload.name,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key,
            api_key_env=payload.api_key_env,
        )

    @router.get("/models")
    def models(base_url: str, family: str = "openai", api_key: str = "") -> dict:
        """Modelos que ofrece esa URL, con el más usado sugerido (como la TUI)."""
        return backend.models(base_url=base_url, family=family, api_key=api_key)

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
        return backend.apply_role(payload.role, payload.provider, payload.model)

    @router.get("/doctor")
    def doctor() -> dict:
        return {"checks": backend.doctor()}

    @router.post("/setup")
    def setup_sandbox() -> dict:
        return backend.setup_sandbox()

    @router.get("/stats")
    def stats() -> dict:
        return backend.stats()

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
