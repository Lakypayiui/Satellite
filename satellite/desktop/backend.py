"""Estado del backend de escritorio: un runtime por proyecto, token y puente de eventos.

Nada de lógica de agente aquí: todo se delega en ``Session``/``build_runtime``, el mismo
runtime que usan la CLI y la TUI, sobre el mismo ``.satellite/``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from satellite.desktop.approvals import DesktopApproval, decision_from
from satellite.desktop.auth import new_token
from satellite.desktop.events import EventBridge
from satellite.orchestration.sessions import SessionStore
from satellite.persistence.changes import ChangeLedger
from satellite.persistence.store import FileStore
from satellite.tools.jobs import registry_for
from satellite.tui.session import Session

RECENTS_LIMIT = 12
SECRET_KEYS = {"api_key", "apikey", "api_key_value", "token", "password", "secret"}


class BackendError(RuntimeError):
    """Error con código y estado HTTP: la app dice qué pasa en vez de un 500 mudo."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def to_jsonable(value: Any) -> Any:
    """Convierte lo que devuelve el runtime a algo serializable, sin inventar datos."""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_jsonable(to_dict())
    return str(value)


def redact(data: Any) -> Any:
    """Quita secretos de una configuración: la clave del proveedor no sale de aquí."""
    if isinstance(data, dict):
        out: dict = {}
        for key, value in data.items():
            if str(key).strip().lower() in SECRET_KEYS:
                out[f"{key}_set"] = bool(value)
                continue
            out[str(key)] = redact(value)
        return out
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data


class DesktopBackend:
    """Lo que la app necesita, sobre el runtime y el almacén de siempre."""

    def __init__(
        self,
        project_root=None,
        *,
        home=None,
        token: str = "",
        session_factory=None,
        recents_path=None,
    ) -> None:
        self.token = token or new_token()
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self._home = Path(home) if home is not None else None
        self._factory = session_factory or Session
        self._sessions: dict[str, Any] = {}
        self._approvals: dict[str, DesktopApproval] = {}
        self._bridges: dict[str, EventBridge] = {}
        self._runtime_errors: dict[str, str] = {}
        self._stop: asyncio.Event | None = None
        self._turnos: dict[str, asyncio.Task] = {}
        self._cancelados: set[str] = set()
        self._recents_path = (
            Path(recents_path)
            if recents_path is not None
            else (self._home if self._home is not None else Path.home() / ".satellite")
            / "desktop"
            / "projects.json"
        )
        self.remember(self.project_root)

    # --- proyectos ---

    def resolve(self, project_root=None) -> Path:
        """Carpeta del proyecto en cuestión (la abierta si no se dice otra)."""
        return Path(project_root or self.project_root).resolve()

    def session(self, project_root=None):
        """Sesión del proyecto, creada una sola vez (barata: no arma el runtime)."""
        root = self.resolve(project_root)
        key = str(root)
        session = self._sessions.get(key)
        if session is None:
            session = self._factory(root, home=self._home)
            self._sessions[key] = session
        return session

    def open_project(self, path) -> dict:
        """Abre o cambia de carpeta: se valida, se prepara lo mínimo y se recuerda."""
        root = Path(str(path)).expanduser()
        if not root.is_dir():
            raise BackendError("project_not_found", f"no es una carpeta: {root}", 404)
        self.project_root = root.resolve()
        try:
            self.session().paths()  # crea .satellite/ de forma idempotente
        except Exception as exc:  # noqa: BLE001 — se informa, no se finge que está listo
            raise BackendError(
                "project_unavailable", f"no se pudo preparar el proyecto: {exc}", 503
            ) from exc
        self.remember(self.project_root)
        return self.status()

    def recents(self) -> list[str]:
        """Proyectos abiertos antes (los últimos primero)."""
        try:
            data = json.loads(self._recents_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [str(item) for item in data if isinstance(item, str)]

    def remember(self, root: Path) -> None:
        items = [item for item in self.recents() if item != str(root)]
        items.insert(0, str(root))
        try:
            self._recents_path.parent.mkdir(parents=True, exist_ok=True)
            self._recents_path.write_text(
                json.dumps(items[:RECENTS_LIMIT], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return

    # --- runtime y eventos ---

    def bridge(self, project_root=None) -> EventBridge:
        """Puente de eventos del proyecto, enganchado en cuanto hay runtime."""
        key = str(self.resolve(project_root))
        bridge = self._bridges.get(key)
        if bridge is None:
            bridge = EventBridge()
            self._bridges[key] = bridge
        if not bridge.attached and key not in self._runtime_errors:
            try:
                _orchestrator, bus, store, _registry = self.session(project_root).runtime()
                bridge.attach(bus, store)
            except Exception as exc:  # noqa: BLE001 — sin proveedor no hay runtime: se dice
                self._runtime_errors[key] = str(exc)
        return bridge

    def require_runtime(self, project_root=None) -> EventBridge:
        """Como ``bridge``, pero exige runtime y explica el motivo si no lo hay."""
        bridge = self.bridge(project_root)
        if not bridge.attached:
            motivo = self.runtime_error(project_root) or "el runtime no está disponible"
            raise BackendError("runtime_unavailable", motivo, 503)
        return bridge

    def runtime_error(self, project_root=None) -> str:
        return self._runtime_errors.get(str(self.resolve(project_root)), "")

    def stream_stop(self) -> asyncio.Event:
        """Evento que cierra los flujos SSE al apagar el backend."""
        if self._stop is None:
            self._stop = asyncio.Event()
        return self._stop

    # --- estado ---

    def status(self, project_root=None) -> dict:
        session = self.session(project_root)
        try:
            data = to_jsonable(session.status())
        except Exception as exc:  # noqa: BLE001
            raise BackendError(
                "status_unavailable", f"no se pudo leer el estado: {exc}", 503
            ) from exc
        data["runtime_ready"] = self.bridge(project_root).attached
        error = self.runtime_error(project_root)
        if error:
            data["runtime_error"] = error
        return data

    # --- aprobaciones ---

    def approval(self, project_root=None) -> DesktopApproval:
        key = str(self.resolve(project_root))
        port = self._approvals.get(key)
        if port is None:
            port = DesktopApproval()
            self._approvals[key] = port
        return port

    def pending_approvals(self, project_root=None) -> list[dict]:
        return self.approval(project_root).describe()

    def resolve_approval(
        self, proposal_id: str, decision: str, *, feedback: str = "", reason: str = ""
    ) -> dict:
        try:
            parsed = decision_from(decision, feedback=feedback, reason=reason)
        except ValueError as exc:
            raise BackendError("invalid_decision", str(exc), 422) from exc
        port = self.approval()
        if not port.is_pending(proposal_id):
            raise BackendError(
                "not_pending", f"no hay una propuesta pendiente con id {proposal_id!r}", 404
            )
        port.submit(proposal_id, parsed)
        return {"proposal_id": proposal_id, "decision": parsed.to_dict()}

    # --- sesiones de conversación ---

    def sessions(self, project_root=None) -> list[dict]:
        store = SessionStore(self.resolve(project_root))
        return [to_jsonable(info) for info in store.list()]

    def sessions_payload(self, project_root=None) -> dict:
        """Lista de sesiones y cuál está activa (sin armar el runtime)."""
        store = SessionStore(self.resolve(project_root))
        return {
            "current": store.active_id(),
            "sessions": [to_jsonable(info) for info in store.list()],
        }

    def new_session(self, name: str = "", project_root=None) -> dict:
        self.require_runtime(project_root)
        session_id = self.session(project_root).new_session(name)
        return {"session_id": session_id, "sessions": self.sessions(project_root)}

    def activate_session(self, target: str, project_root=None) -> dict:
        self.require_runtime(project_root)
        session = self.session(project_root)
        session_id = session.use_session(target)
        if session_id is None:
            raise BackendError("session_not_found", f"no existe la sesión {target!r}", 404)
        return {
            "session_id": session_id,
            "turns": self.session_turns(session_id, project_root),
        }

    def rename_session(self, session_id: str, name: str, project_root=None) -> dict:
        if not name.strip():
            raise BackendError("invalid_request", "el nombre no puede estar vacío", 422)
        store = SessionStore(self.resolve(project_root))
        if store.find(session_id) is None:
            raise BackendError("session_not_found", f"no existe la sesión {session_id!r}", 404)
        store.rename(session_id, name.strip())
        return {"session_id": session_id, "name": name.strip()}

    def session_turns(self, session_id: str, project_root=None) -> list[dict]:
        _info, turns = SessionStore(self.resolve(project_root)).read(session_id)
        return turns

    # --- conversación ---

    async def send(self, text: str, *, plan_mode: bool = False, project_root=None) -> dict:
        """Un turno de conversación con el runtime real, aprobaciones incluidas.

        El turno corre como tarea propia para que se pueda cancelar: ``cancel_turn`` la
        corta y aquí se convierte en una respuesta normal, sin inventar que la tarea salió bien.
        """
        bridge = self.require_runtime(project_root)
        key = str(self.resolve(project_root))
        tarea = asyncio.create_task(
            self.session(project_root).send_message(
                text, approval=self.approval(project_root), plan_mode=plan_mode
            )
        )
        self._turnos[key] = tarea
        try:
            reply = await tarea
        except asyncio.CancelledError:
            if key not in self._cancelados:
                raise  # no lo hemos cancelado nosotros: la cancelación sigue su curso
            self._cancelados.discard(key)
            await bridge.drain()
            return {
                "text": "",
                "status": "cancelled",
                "cancelled": True,
                "note": "turno cancelado; lo que ya se hizo queda en el historial",
            }
        finally:
            if self._turnos.get(key) is tarea:
                self._turnos.pop(key, None)
        await bridge.drain()
        return to_jsonable(reply)

    CANCEL_GRACE_S = 2.5

    async def cancel_turn(self, project_root=None) -> dict:
        """Cancela el turno en curso: primero se le pide al runtime, y si no cede, se corta.

        Se dice siempre cuál de los dos caminos fue, porque no significan lo mismo: la
        cancelación cooperativa para el runtime entre nodos y deja el estado contable; el
        corte duro interrumpe lo que esté en vuelo.
        """
        key = str(self.resolve(project_root))
        tarea = self._turnos.get(key)
        if tarea is None or tarea.done():
            raise BackendError("no_turn", "no hay ningún turno en curso", 409)
        # Los permisos que estaban esperando decisión se quedan sin quien los espere: se
        # rechazan para que la interfaz no muestre tarjetas fantasma.
        port = self.approval(project_root)
        for item in port.pending():
            port.submit(
                str(item.get("proposal_id", "")),
                decision_from("reject", reason="turno cancelado"),
            )
        pedido = self.request_cancel(project_root)
        if pedido:
            try:
                await asyncio.wait_for(asyncio.shield(tarea), timeout=self.CANCEL_GRACE_S)
                return {
                    "cancelled": True,
                    "how": "cooperative",
                    "note": "el runtime paró entre nodos; lo hecho queda en el historial",
                }
            except Exception:  # noqa: BLE001 — no cedió a tiempo (o terminó mal): se corta
                pass
        self._cancelados.add(key)
        tarea.cancel()
        return {
            "cancelled": True,
            "how": "cooperative" if pedido else "hard",
            "note": (
                "el runtime no cedió en "
                f"{self.CANCEL_GRACE_S:.1f}s y corté la tarea; lo que estaba en vuelo "
                "puede haber seguido un momento"
            ),
        }

    def request_cancel(self, project_root=None) -> bool:
        """Pide la cancelación al runtime. ``False`` si este runtime no la soporta."""
        try:
            orchestrator = self.session(project_root).runtime()[0]
        except Exception:  # noqa: BLE001 — sin runtime no hay nada que cancelar
            return False
        cancel = getattr(orchestrator, "cancel", None)
        if not callable(cancel):
            return False
        return bool(cancel("", "cancelado desde la app").get("cancelled"))

    # --- tareas, cambios y trabajos ---

    async def tasks(self, project_root=None) -> dict:
        root = self.resolve(project_root)
        store = FileStore(root)
        ledger = ChangeLedger(root)
        out: list[dict] = []
        for task_id in store.list_tasks():
            item: dict = {"task_id": task_id, "changes": ledger.files(task_id)}
            try:
                task = await store.load_task(task_id)
                item["goal"] = str(getattr(task, "goal", ""))
                state = await store.load_state(task_id)
                item["status"] = str((state or {}).get("status", ""))
            except Exception as exc:  # noqa: BLE001 — una tarea ilegible no oculta las demás
                item["error"] = str(exc)
            out.append(item)
        out.sort(key=lambda item: (not item["changes"], item["task_id"]))
        return {"tasks": out, "latest": ledger.latest_task() or ""}

    async def task_detail(self, task_id: str, project_root=None) -> dict:
        root = self.resolve(project_root)
        store = FileStore(root)
        if task_id not in store.list_tasks():
            raise BackendError("task_not_found", f"no existe la tarea {task_id!r}", 404)
        ledger = ChangeLedger(root)
        detail: dict = {"task_id": task_id}
        detail["task"] = to_jsonable(await store.load_task(task_id))
        try:
            detail["graph"] = to_jsonable(await store.load_graph(task_id))
        except Exception as exc:  # noqa: BLE001 — sin grafo guardado se dice, no se rellena
            detail["graph_error"] = str(exc)
        detail["state"] = await store.load_state(task_id)
        detail["events"] = [to_jsonable(event) for event in await store.load_events(task_id)]
        detail["changes"] = {
            "files": ledger.files(task_id),
            "entries": to_jsonable(ledger.entries(task_id)),
        }
        return detail

    def changes(self, task_id: str, project_root=None) -> dict:
        ledger = ChangeLedger(self.resolve(project_root))
        if not ledger.has(task_id):
            raise BackendError(
                "no_changes", f"no hay cambios registrados para {task_id!r}", 404
            )
        return {
            "task_id": task_id,
            "files": ledger.files(task_id),
            "entries": to_jsonable(ledger.entries(task_id)),
            "diff": ledger.diff(task_id),
        }

    def restore(self, task_id: str, *, confirm: bool = False, project_root=None) -> dict:
        """Restaura los cambios de una tarea; sin ``confirm`` sólo dice qué se descartará."""
        ledger = ChangeLedger(self.resolve(project_root))
        if not ledger.has(task_id):
            raise BackendError(
                "no_changes", f"no hay cambios registrados para {task_id!r}", 404
            )
        preview = {
            "task_id": task_id,
            "files": ledger.files(task_id),
            "entries": to_jsonable(ledger.entries(task_id)),
            "diff": ledger.diff(task_id),
        }
        if not confirm:
            return {
                "confirmed": False,
                "preview": preview,
                "note": "no se ha restaurado nada: falta confirmar",
            }
        result = to_jsonable(ledger.restore(task_id))
        return {"confirmed": True, "preview": preview, "result": result}

    def jobs(self, project_root=None) -> list[dict]:
        return [info.to_dict() for info in registry_for(self.resolve(project_root)).list()]

    def job_output(self, job_id: str, project_root=None) -> dict:
        registry = registry_for(self.resolve(project_root))
        info = registry.status(job_id)
        if info is None:
            raise BackendError("job_not_found", f"no existe el trabajo {job_id!r}", 404)
        return {
            **info.to_dict(),
            "output": registry.output(job_id),
            "controllable": info.status != "desconocido",
        }

    def job_stop(self, job_id: str, project_root=None) -> dict:
        registry = registry_for(self.resolve(project_root))
        if registry.status(job_id) is None:
            raise BackendError("job_not_found", f"no existe el trabajo {job_id!r}", 404)
        info = registry.stop(job_id)
        if info is None:
            raise BackendError(
                "job_foreign", f"no puedo parar {job_id!r}: lo lanzó otro proceso", 409
            )
        return info.to_dict()

    # --- catálogos y configuración ---

    def agents(self, project_root=None) -> list[dict]:
        return [to_jsonable(spec) for spec in self.session(project_root).agents()]

    def skills(self, project_root=None) -> list[dict]:
        return [to_jsonable(manifest) for manifest in self.session(project_root).skills()]

    def config(self, project_root=None) -> dict:
        session = self.session(project_root)
        settings = session.settings()
        return {
            "config": redact(session.config_data()),
            "policy_mode": settings.policy_mode.value,
            "sandbox": settings.sandbox_level,
            "providers": [to_jsonable(item) for item in session.saved_providers()],
            "active_provider": session.active_provider_id(),
        }

    def update_config(
        self, *, policy_mode: str = "", safe_mode: bool | None = None, project_root=None
    ) -> dict:
        session = self.session(project_root)
        if policy_mode:
            mode = policy_mode.strip().lower()
            if mode not in ("ask", "auto", "unrestricted"):
                raise BackendError(
                    "invalid_policy", f"modo de política desconocido: {policy_mode!r}", 422
                )
            session.set_mode(mode)
        if safe_mode is not None:
            session.set_safe_mode(bool(safe_mode))
        return self.config(project_root)

    def save_provider(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str = "",
        api_key_env: str = "",
        project_root=None,
    ) -> dict:
        session = self.session(project_root)
        if not name.strip() or not model.strip() or not base_url.strip():
            raise BackendError(
                "invalid_provider", "nombre, modelo y URL son obligatorios", 422
            )
        session.save_provider(
            name=name.strip(),
            model=model.strip(),
            base_url=base_url.strip(),
            api_key=api_key,
            api_key_env=api_key_env,
        )
        return self.config(project_root)

    def models(
        self,
        *,
        base_url: str,
        family: str = "openai",
        api_key: str = "",
        project_root=None,
    ) -> dict:
        """Lista los modelos de esa URL y sugiere el más usado, como el asistente del CLI."""
        from satellite.config.models import default_model

        if not base_url.strip():
            raise BackendError("invalid_request", "falta 'base_url'", 422)
        result = self.session(project_root).load_models(
            base_url.strip(), family or "openai", api_key
        )
        data = to_jsonable(result)
        if isinstance(data, dict):
            modelos = data.get("models") if isinstance(data.get("models"), list) else []
            data["suggested"] = default_model([str(item) for item in modelos]) if modelos else ""
        return data

    # --- cierre ---

    async def close(self) -> None:
        """Cierra ordenadamente: corta los flujos y espera las escrituras pendientes."""
        if self._stop is not None:
            self._stop.set()
        for bridge in self._bridges.values():
            await bridge.drain()

    # --- catálogo de proveedores y roles (lo mismo que hace el asistente del CLI) ---

    def provider_catalog(self) -> list[dict]:
        """Catálogo con la URL ya puesta, para no teclearla (como hace el CLI)."""
        from satellite.config import catalog

        return [
            {
                "key": entry.key,
                "label": entry.label,
                "family": entry.family,
                "base_url": entry.base_url,
                "group": entry.group,
                "note": entry.note,
                "needs_edit": entry.needs_edit,
            }
            for entry in catalog.CATALOG
        ]

    def roles(self, project_root=None) -> dict:
        """Roles configurados por separado, y a qué proveedor apunta cada uno."""
        from satellite.config import providers as pconfig

        root = self.resolve(project_root)
        return {
            "roles": list(pconfig.ROLES),
            "configured": to_jsonable(pconfig.role_config(root)),
            "active": pconfig.active_id(root),
            "providers": [to_jsonable(item) for item in pconfig.list_providers(root)],
        }

    def apply_role(
        self, role: str, provider_id: str, model: str = "", project_root=None
    ) -> dict:
        """Fija un rol a un proveedor guardado. Sin cascada: sólo ese rol."""
        from satellite.config import providers as pconfig

        root = self.resolve(project_root)
        if role not in pconfig.ROLES:
            raise BackendError(
                "invalid_role", f"rol desconocido: {role!r} (roles: {', '.join(pconfig.ROLES)})", 422
            )
        if not provider_id:
            pconfig.clear_roles(root, [role])
        else:
            if pconfig.find_provider(root, provider_id) is None:
                raise BackendError(
                    "provider_not_found", f"no existe el proveedor {provider_id!r}", 404
                )
            section: dict = {"provider": provider_id}
            if model.strip():
                section["model"] = model.strip()
            pconfig.set_roles(root, {role: section})
        # La configuración de rol tiene que cambiar lo que de verdad se ejecuta, no quedarse
        # escrita esperando a un reinicio: se reconstruye el runtime en el sitio.
        try:
            self.session(project_root).runtime(force=True)
        except Exception as exc:  # noqa: BLE001 — el rol queda guardado y se dice el resto
            return {**self.roles(project_root), "runtime_error": str(exc)}
        return self.roles(project_root)

    # --- mantenimiento: lo que en la TUI son /doctor, /setup, /stats, /context… ---

    def doctor(self, project_root=None) -> list[dict]:
        return [
            {"ok": bool(ok), "mensaje": mensaje}
            for ok, mensaje in self.session(project_root).doctor()
        ]

    def setup_sandbox(self, project_root=None) -> dict:
        return to_jsonable(self.session(project_root).setup_sandbox())

    def stats(self, project_root=None) -> dict:
        session = self.session(project_root)
        return {
            "skills": to_jsonable(session.stats()),
            "cache": to_jsonable(session.cache_report()),
        }

    def snapshots(self, project_root=None) -> dict:
        return {"snapshots": self.session(project_root).snapshots()}

    def restore_snapshot(self, name: str, *, confirm: bool = False, project_root=None) -> dict:
        if not confirm:
            return {
                "confirmed": False,
                "name": name,
                "note": "no se ha restaurado nada: falta confirmar que se descarta el estado actual",
            }
        resultado = self.session(project_root).restore(name)
        return {"confirmed": True, "name": name, "result": to_jsonable(resultado)}

    def reset_project(
        self, *, keep_skills: bool = True, confirm: bool = False, project_root=None
    ) -> dict:
        if not confirm:
            return {
                "confirmed": False,
                "note": "no se ha tocado nada: esto borra agentes y skills creados",
            }
        resultado = self.session(project_root).reset(keep_skills=keep_skills)
        return {"confirmed": True, "result": to_jsonable(resultado)}

    async def compact(self, *, keep: int | None = None, project_root=None) -> dict:
        resultado = await self.session(project_root).compact_context(keep=keep)
        return to_jsonable(resultado)

    # --- skills y agentes (lo que en la TUI son /skill-*, /curate, /create) ---

    def skill_search(self, query: str, project_root=None) -> list[dict]:
        return [to_jsonable(item) for item in self.session(project_root).skill_search(query)]

    def skill_edit(
        self,
        skill_id: str,
        *,
        description: str | None = None,
        tags: list[str] | None = None,
        project_root=None,
    ) -> dict:
        manifest = self.session(project_root).skill_edit(
            skill_id, description=description, tags=tuple(tags) if tags else None
        )
        return to_jsonable(manifest)

    def skill_delete(self, skill_id: str, project_root=None) -> dict:
        self.session(project_root).skill_delete(skill_id)
        return {"deleted": skill_id}

    async def curate(self, request: str = "", project_root=None) -> dict:
        self.require_runtime(project_root)
        outcome = await self.session(project_root).curate(
            request, approval=self.approval(project_root)
        )
        return to_jsonable(outcome)

    async def create_agent(self, need: str, project_root=None) -> dict:
        self.require_runtime(project_root)
        outcome = await self.session(project_root).create_agent(
            need, approval=self.approval(project_root)
        )
        return to_jsonable(outcome)
