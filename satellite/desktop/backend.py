"""Estado del backend de escritorio: un runtime por proyecto, token y puente de eventos.

Nada de lógica de agente aquí: todo se delega en ``Session``/``build_runtime``, el mismo
runtime que usan la CLI y la TUI, sobre el mismo ``.satellite/``.
"""

from __future__ import annotations

import asyncio
import inspect
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
# Autocompletado de @menciones del compositor: tope de sugerencias y vida de la caché
# del listado (evita recorrer el árbol en cada tecla sin quedarse con datos viejos).
MENTION_LIMIT = 20
MENTION_CACHE_S = 5.0


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
        self._index_cache: dict[str, tuple[float, list[dict]]] = {}
        self._stop: asyncio.Event | None = None
        # Servicio Safe Proxy embebido (se crea a la primera y se para al cerrar).
        self._safe_proxy_manager = None
        # Si el proyecto lo eligió el usuario (para el onboarding) y alternativas del modo
        # plan pendientes de elección, por proyecto.
        self._explicit_project = project_root is not None
        self._plans: dict[str, Any] = {}
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
        self._explicit_project = True
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

    @staticmethod
    def _force_runtime(session):
        """``session.runtime(force=True)`` sin romper con sesiones que no aceptan ``force``.

        La ``Session`` real acepta ``force`` y es lo único que garantiza rearmar de verdad.
        Otras implementaciones pueden no tenerlo: entonces se llama sin ``force`` en vez de
        reventar con un ``TypeError``. Se decide **por firma**, no capturando el ``TypeError``
        de la llamada: así un error real al construir el runtime se sigue viendo, en vez de
        confundirse con "no admite force".
        """
        try:
            parametros = inspect.signature(session.runtime).parameters
        except (TypeError, ValueError):
            parametros = {}
        admite = "force" in parametros or any(
            parametro.kind is inspect.Parameter.VAR_KEYWORD
            for parametro in parametros.values()
        )
        return session.runtime(force=True) if admite else session.runtime()

    def _rebuild_runtime(self, project_root=None) -> str:
        """Rearma el runtime y vuelve a enganchar el puente de eventos al bus nuevo.

        Sin esto, cambiar de proveedor/rol/modo dejaba el puente escuchando el bus viejo:
        el chat seguía funcionando pero la actividad en vivo se quedaba muda. Devuelve el
        error (cadena vacía si fue bien) para poder decirlo sin romper el flujo.
        """
        key = str(self.resolve(project_root))
        try:
            _orchestrator, bus, store, _registry = self._force_runtime(self.session(project_root))
        except Exception as exc:  # noqa: BLE001 — queda guardado y se informa aparte
            self._runtime_errors[key] = str(exc)
            return str(exc)
        bridge = self._bridges.get(key)
        if bridge is not None:
            bridge.attach(bus, store)
        self._runtime_errors.pop(key, None)
        return ""

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
        # Para el onboarding: si el proyecto lo eligió una persona y si hay proveedor,
        # en vez de deducirlo de mensajes de error.
        data["project_explicit"] = self._explicit_project
        data["provider_configured"] = self._provider_configured(project_root)
        error = self.runtime_error(project_root)
        if error:
            data["runtime_error"] = error
        return data

    # --- aprobaciones ---

    def _policy_mode(self, project_root=None) -> str:
        """Modo de política efectivo (``ask`` si no se puede leer: el lado seguro)."""
        try:
            return self.session(project_root).settings().policy_mode.value
        except Exception:  # noqa: BLE001
            return "ask"

    def _approval_for(self, project_root=None):
        """Puerto de aprobación según el modo, como la TUI y la CLI.

        En ``unrestricted`` no se pregunta nada (``AutoApprovalPort``); en ``ask`` y
        ``auto`` se pregunta por lo que el guard no resuelve solo. Así lo que muestra la
        app (el modo) es lo que de verdad se aplica: no se simula un modo más permisivo.
        """
        if self._policy_mode(project_root) == "unrestricted":
            from satellite.approval.base import AutoApprovalPort

            return AutoApprovalPort()
        return self.approval(project_root)

    def approval(self, project_root=None) -> DesktopApproval:
        key = str(self.resolve(project_root))
        port = self._approvals.get(key)
        if port is None:
            port = DesktopApproval()
            self._approvals[key] = port
        return port

    def pending_approvals(self, project_root=None) -> list[dict]:
        """Permisos esperando decisión (en ``unrestricted`` nunca hay: no se pregunta)."""
        if self._policy_mode(project_root) == "unrestricted":
            return []
        return self.approval(project_root).describe()

    def _provider_configured(self, project_root=None) -> bool:
        """True si hay un proveedor resoluble (el que el runtime usaría)."""
        from satellite.config import providers as pconfig

        try:
            return pconfig.active_provider(self.resolve(project_root)) is not None
        except Exception:  # noqa: BLE001 — config ilegible = no hay proveedor utilizable
            return False

    def _require_project(self) -> None:
        """Sin proyecto elegido no se ejecuta nada sobre la carpeta implícita.

        Antes de elegir carpeta, el "proyecto" sería la del propio Satellite (el cwd del
        proceso) y el agente podría tocar el código de la app. Se bloquea la **ejecución**
        (no la UI) con un aviso claro. Elegir la carpeta de Satellite a propósito cuenta como
        elección y desbloquea, así que el bloqueo no impide ese caso legítimo.
        """
        if self._explicit_project:
            return
        raise BackendError(
            "no_project",
            "no hay proyecto elegido: abre una carpeta en el asistente (Ctrl+K · onboarding) "
            "o «cambiar carpeta» antes de ejecutar tareas, para no trabajar sobre la carpeta "
            "de Satellite",
            409,
        )

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
        self._require_project()
        self.require_runtime(project_root)
        self._plans.pop(str(self.resolve(project_root)), None)
        session_id = self.session(project_root).new_session(name)
        return {"session_id": session_id, "sessions": self.sessions(project_root)}

    def activate_session(self, target: str, project_root=None) -> dict:
        self.require_runtime(project_root)
        session = self.session(project_root)
        session_id = session.use_session(target)
        if session_id is None:
            raise BackendError("session_not_found", f"no existe la sesión {target!r}", 404)
        # Las alternativas del modo plan eran de la conversación anterior: no se arrastran.
        self._plans.pop(str(self.resolve(project_root)), None)
        return {
            "session_id": session_id,
            "turns": self.session_turns(session_id, project_root),
        }

    def update_session(
        self,
        session_id: str,
        *,
        name: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        project_root=None,
    ) -> dict:
        """Cambia nombre, fijado y/o archivado de una sesión (sólo lo que llega).

        Fijar no cambia la activa. Archivar la activa mueve la actividad a otra conversación
        sin archivar (primero las fijadas, después por recencia); si no queda ninguna, la
        activa se deja apuntando a la archivada y la app muestra el estado vacío. Nunca se
        borran turnos: archivar es sólo una marca.
        """
        store = SessionStore(self.resolve(project_root))
        if store.find(session_id) is None:
            raise BackendError("session_not_found", f"no existe la sesión {session_id!r}", 404)
        if name is not None:
            if not name.strip():
                raise BackendError("invalid_request", "el nombre no puede estar vacío", 422)
            store.rename(session_id, name.strip())
        if pinned is not None:
            store.set_pinned(session_id, pinned)
        if archived is not None:
            store.set_archived(session_id, archived)
            if archived and store.active_id() == session_id:
                self._rehome_active(store, session_id, project_root)
        info, _turns = store.read(session_id)
        return {
            "session_id": session_id,
            "name": info.name,
            "pinned": info.pinned,
            "archived": info.archived,
            "current": store.active_id(),
            "sessions": self.sessions(project_root),
        }

    def _rehome_active(self, store: SessionStore, archived_id: str, project_root=None) -> None:
        """Deja como activa otra conversación sin archivar, si queda alguna.

        La elección (fijadas primero, luego recencia) es la del almacén, compartida con la CLI
        y la TUI. Se prefiere mover el puntero a través de la sesión de trabajo
        (``use_session``) para que el historial en memoria quede coherente; si el runtime no
        está disponible, basta con mover el puntero del almacén: el chat se recargará al armarse.
        """
        destino = store.next_active_after_archive(archived_id)
        if destino is None:
            return
        # Las alternativas del modo plan eran de la conversación anterior: no se arrastran.
        self._plans.pop(str(self.resolve(project_root)), None)
        try:
            session = self.session(project_root)
            if session.use_session(destino) is None:
                store.set_active(destino)
        except Exception:  # noqa: BLE001 — sin runtime: mover el puntero es suficiente
            store.set_active(destino)

    def session_turns(self, session_id: str, project_root=None) -> list[dict]:
        _info, turns = SessionStore(self.resolve(project_root)).read(session_id)
        return turns

    # --- modo plan (alternativas para elegir) ---

    def _remember_plan(self, reply, project_root=None) -> dict | None:
        """Recuerda las alternativas del modo plan, o las olvida si la respuesta no trae."""
        key = str(self.resolve(project_root))
        output = getattr(reply, "output", None)
        payload: dict | None = None
        if isinstance(output, dict) and output.get("mode") == "plan_options":
            candidate = output.get("plan_options")
            payload = candidate if isinstance(candidate, dict) else None
        if payload is None:
            self._plans.pop(key, None)
            return None
        try:
            from satellite.core.plan import PlanOptions

            options = PlanOptions.from_dict(payload)
        except Exception:  # noqa: BLE001 — datos incompletos: se ignora, no se inventa
            self._plans.pop(key, None)
            return None
        self._plans[key] = options
        return options.to_dict()

    def pending_plan(self, project_root=None) -> dict | None:
        """Alternativas pendientes de elección (las que la app debe ofrecer), o ``None``."""
        options = self._plans.get(str(self.resolve(project_root)))
        return options.to_dict() if options is not None else None

    async def choose_plan(self, option_id: str, *, project_root=None) -> dict:
        """Elige una alternativa del modo plan y continúa (igual que ``ChatSession.choose``)."""
        self._require_project()
        bridge = self.require_runtime(project_root)
        key = str(self.resolve(project_root))
        options = self._plans.get(key)
        if options is None:
            raise BackendError(
                "no_plan",
                "no hay alternativas pendientes: vuelve a plantear la tarea",
                409,
            )
        option = options.option(option_id)
        if option is None:
            raise BackendError(
                "unknown_option", f"alternativa desconocida: {option_id!r}", 404
            )
        reply = await self.session(project_root).choose_option(
            option, approval=self._approval_for(project_root)
        )
        # La elección consume las alternativas: no se vuelven a ofrecer.
        self._plans.pop(key, None)
        await bridge.drain()
        return {**to_jsonable(reply), "plan": None}

    # --- conversación ---

    async def send(self, text: str, *, plan_mode: bool = False, project_root=None) -> dict:
        """Un turno de conversación con el runtime real, aprobaciones incluidas.

        El turno corre como tarea propia para que se pueda cancelar: ``cancel_turn`` la
        corta y aquí se convierte en una respuesta normal, sin inventar que la tarea salió bien.
        """
        self._require_project()
        bridge = self.require_runtime(project_root)
        key = str(self.resolve(project_root))
        # Las @menciones se resuelven igual que en la TUI (el propio ChatSession las vuelve a
        # parsear): aquí se calculan además para poder decir qué se adjuntó y qué no existía.
        contexto = self._mention_context(text, project_root)
        tarea = asyncio.create_task(
            self.session(project_root).send_message(
                text, approval=self._approval_for(project_root), plan_mode=plan_mode
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
                "context": contexto,
                "plan": None,
            }
        finally:
            if self._turnos.get(key) is tarea:
                self._turnos.pop(key, None)
        await bridge.drain()
        # El modo plan devuelve alternativas: se guardan para poder elegir después (incluso
        # tras recargar la app). Cualquier otra respuesta olvida las anteriores.
        plan = self._remember_plan(reply, project_root)
        return {**to_jsonable(reply), "context": contexto, "plan": plan}

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
            # Aquí el método fue el corte, aunque antes se pidiera la vía cooperativa: si
            # se llegó hasta aquí es porque el runtime no cedió. Se dice aparte que se pidió.
            "how": "hard",
            "cooperative_requested": pedido,
            "note": (
                f"el runtime no cedió en {self.CANCEL_GRACE_S:.1f}s y corté la tarea; "
                "lo que estaba en vuelo puede haber seguido un momento"
                if pedido
                else "este runtime no ofrece cancelación cooperativa: corté la tarea "
                "directamente; lo que estaba en vuelo puede haber seguido un momento"
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

    async def job_wait(self, job_id: str, *, timeout_s: float = 30.0, project_root=None) -> dict:
        """Espera a que un trabajo termine sin bloquear la app (como ``/jobs wait``)."""
        registry = registry_for(self.resolve(project_root))
        if registry.status(job_id) is None:
            raise BackendError("job_not_found", f"no existe el trabajo {job_id!r}", 404)
        info, estado = await registry.wait(job_id, timeout_s)
        if estado == "ajeno":
            raise BackendError(
                "job_foreign", f"no puedo esperar {job_id!r}: lo lanzó otro proceso", 409
            )
        return {
            "job_id": job_id,
            "estado": estado,
            "status": info.status if info is not None else "",
        }

    # --- git y worktrees ---

    def git_status(self, project_root=None) -> dict:
        """Estado real del repo en el **workdir de ejecución** activo (o el proyecto)."""
        from satellite import gitops

        root = self.resolve(project_root)
        workdir = gitops.active_workdir(root) or root
        try:
            data = gitops.status(workdir)
        except gitops.GitError as exc:
            return {
                "is_repo": False,
                "error": str(exc),
                "root": str(root),
                "workdir": str(workdir),
            }
        data["root"] = str(root)
        data["workdir"] = str(workdir)
        return data

    def git_stage(self, paths: list[str], project_root=None) -> dict:
        from satellite import gitops

        return self._git_mutate(lambda root: gitops.stage(root, paths), project_root)

    def git_unstage(self, paths: list[str], project_root=None) -> dict:
        from satellite import gitops

        return self._git_mutate(lambda root: gitops.unstage(root, paths), project_root)

    def git_commit(self, message: str, project_root=None) -> dict:
        from satellite import gitops

        return self._git_mutate(lambda root: gitops.commit(root, message), project_root)

    def _git_mutate(self, accion, project_root=None) -> dict:
        """Ejecuta una mutación git en el workdir activo y devuelve el estado fresco."""
        from satellite import gitops

        self._require_project()
        root = self.resolve(project_root)
        objetivo = gitops.active_workdir(root) or root
        try:
            resultado = accion(objetivo)
        except gitops.GitError as exc:
            raise BackendError("git_error", str(exc), 409) from exc
        return {"result": to_jsonable(resultado), "status": self.git_status(project_root)}

    def worktrees(self, project_root=None) -> dict:
        """Worktrees del repo y cuál está activo como contexto de ejecución."""
        from satellite import gitops

        root = self.resolve(project_root)
        try:
            items = gitops.list_worktrees(root)
            is_repo = gitops.is_repo(root)
        except gitops.GitError as exc:
            return {"is_repo": False, "error": str(exc), "root": str(root), "worktrees": [], "active": ""}
        return {
            "is_repo": is_repo,
            "root": str(root),
            "worktrees": to_jsonable(items),
            "managed_root": str(gitops.worktrees_dir(root)),
            "active": str(gitops.active_workdir(root) or ""),
        }

    def add_worktree(
        self, name: str, *, branch: str = "", base_ref: str = "", project_root=None
    ) -> dict:
        from satellite import gitops

        self._require_project()
        try:
            creado = gitops.add_worktree(
                self.resolve(project_root), name, branch=branch, base_ref=base_ref
            )
        except gitops.GitError as exc:
            raise BackendError("git_error", str(exc), 409) from exc
        return {**self.worktrees(project_root), "created": to_jsonable(creado)}

    def remove_worktree(self, path: str, *, force: bool = False, project_root=None) -> dict:
        from satellite import gitops

        self._require_project()
        root = self.resolve(project_root)
        # Se mira ANTES de borrar: después, el workdir ya no existe y no se podría comparar.
        activo_antes = gitops.active_workdir(root)
        try:
            quitado = gitops.remove_worktree(root, path, force=force)
        except gitops.GitError as exc:
            raise BackendError("git_error", str(exc), 409) from exc
        removido = Path(str(quitado.get("removed", ""))).resolve()
        if activo_antes is not None and activo_antes == removido:
            gitops.set_active_workdir(root, None)
            self._rebuild_runtime(project_root)
        return {**self.worktrees(project_root), "removed": str(quitado.get("removed", ""))}

    def set_worktree(self, path: str, project_root=None) -> dict:
        """Activa (o desactiva con ``""``) el contexto de ejecución: el runtime se rearma."""
        from satellite import gitops

        self._require_project()
        root = self.resolve(project_root)
        try:
            gitops.set_active_workdir(root, path or None)
        except gitops.GitError as exc:
            raise BackendError("git_error", str(exc), 422) from exc
        error = self._rebuild_runtime(project_root)
        payload = self.worktrees(project_root)
        return {**payload, "runtime_error": error} if error else payload

    # --- comentarios inline del diff (persistidos) ---

    def comments(
        self,
        *,
        file: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        include_resolved: bool = True,
        project_root=None,
    ) -> dict:
        from satellite import comments as comments_store

        return {
            "comments": comments_store.list_comments(
                self.resolve(project_root),
                file=file,
                task_id=task_id,
                session_id=session_id,
                include_resolved=include_resolved,
            )
        }

    def add_comment(
        self,
        *,
        file: str,
        line_start: int,
        line_end: int | None = None,
        text: str,
        side: str = "new",
        task_id: str = "",
        session_id: str = "",
        author: str = "",
        project_root=None,
    ) -> dict:
        from satellite import comments as comments_store

        self._require_project()
        try:
            return comments_store.add_comment(
                self.resolve(project_root),
                file=file,
                line_start=line_start,
                line_end=line_end,
                text=text,
                side=side,
                task_id=task_id,
                session_id=session_id,
                author=author,
            )
        except comments_store.CommentError as exc:
            raise BackendError("invalid_comment", str(exc), 422) from exc

    def update_comment(
        self,
        comment_id: str,
        *,
        text: str | None = None,
        resolved: bool | None = None,
        project_root=None,
    ) -> dict:
        from satellite import comments as comments_store

        self._require_project()
        try:
            return comments_store.update_comment(
                self.resolve(project_root), comment_id, text=text, resolved=resolved
            )
        except comments_store.CommentError as exc:
            raise BackendError("invalid_comment", str(exc), 422) from exc
        except KeyError as exc:
            raise BackendError("comment_not_found", f"no existe el comentario {comment_id!r}", 404) from exc

    def delete_comment(self, comment_id: str, project_root=None) -> dict:
        from satellite import comments as comments_store

        self._require_project()
        if not comments_store.delete_comment(self.resolve(project_root), comment_id):
            raise BackendError("comment_not_found", f"no existe el comentario {comment_id!r}", 404)
        return {"deleted": comment_id}

    # --- cola de instrucciones (persistida, ordenada) ---

    def queue(self, project_root=None) -> dict:
        from satellite import prompt_queue

        return {"items": prompt_queue.list_items(self.resolve(project_root))}

    def enqueue(self, text: str, *, plan_mode: bool = False, project_root=None) -> dict:
        from satellite import prompt_queue

        self._require_project()
        try:
            items = prompt_queue.enqueue(self.resolve(project_root), text, plan_mode=plan_mode)
        except prompt_queue.QueueError as exc:
            raise BackendError("invalid_queue_item", str(exc), 422) from exc
        return {"items": items}

    def update_queue_item(
        self, item_id: str, *, text: str, plan_mode: bool | None = None, project_root=None
    ) -> dict:
        from satellite import prompt_queue

        self._require_project()
        try:
            items = prompt_queue.update_item(
                self.resolve(project_root), item_id, text=text, plan_mode=plan_mode
            )
        except prompt_queue.QueueError as exc:
            raise BackendError("invalid_queue_item", str(exc), 422) from exc
        except KeyError as exc:
            raise BackendError("queue_item_not_found", f"no existe el item {item_id!r}", 404) from exc
        return {"items": items}

    def remove_queue_item(self, item_id: str, project_root=None) -> dict:
        from satellite import prompt_queue

        self._require_project()
        try:
            items = prompt_queue.remove_item(self.resolve(project_root), item_id)
        except KeyError as exc:
            raise BackendError("queue_item_not_found", f"no existe el item {item_id!r}", 404) from exc
        return {"items": items}

    def clear_queue(self, project_root=None) -> dict:
        from satellite import prompt_queue

        self._require_project()
        return {"items": prompt_queue.clear(self.resolve(project_root))}

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
            "providers": self._providers_payload(self.resolve(project_root)),
            "active_provider": session.active_provider_id(),
        }

    def desktop_control(self, project_root=None) -> dict:
        """Estado de control de escritorio: flag local + proveedor/modelo de visión.

        El flag vive en la config local del host (``desktop_control.json``); la
        selección de visión vive en ``config.json`` del proyecto y se valida contra
        los proveedores ya guardados. Sólo interfaz: la visión real no se implementa.
        """
        from satellite.desktop_control.config import load_desktop_settings
        from satellite.desktop_control.perception import OCR_INTERFACE, VISION_INTERFACE
        from satellite.desktop_control.vision import resolve_vision_selection

        root = self.resolve(project_root)
        settings = load_desktop_settings()
        selection = resolve_vision_selection(root)
        return {
            "enabled": settings.enabled,
            "vision": {
                "provider": selection.provider,
                "model": selection.model,
                "configured": selection.configured,
                "available": selection.configured,
                "implemented": False,
                "reason": selection.reason,
                "interface": VISION_INTERFACE,
            },
            "ocr": {"available": False, "boxes": True, "interface": OCR_INTERFACE},
            # Recordatorio para la UI: la selección no concede nada.
            "guards": {
                "approvals": True,
                "grants": True,
                "limits": True,
                "audit": True,
                "mcp": False,
            },
        }

    def update_desktop_control(
        self,
        *,
        enabled: bool | None = None,
        vision_provider: str | None = None,
        vision_model: str | None = None,
        project_root=None,
    ) -> dict:
        """Activa/desactiva (host local) y/o elige proveedor/modelo de visión (config).

        Escribir esto **no** concede permisos: los gates de F4 (aprobación, grants,
        topes y auditoría) siguen aplicándose. MCP no tiene esta ruta.
        """
        from dataclasses import replace as dataclass_replace

        from satellite.config import providers as provider_config
        from satellite.desktop_control.config import (
            load_desktop_settings,
            write_desktop_settings,
        )
        from satellite.desktop_control.vision import (
            resolve_vision_selection,
            save_selection,
        )

        root = self.resolve(project_root)
        changed = False
        if enabled is not None:
            settings = load_desktop_settings()
            if bool(enabled) != settings.enabled:
                write_desktop_settings(
                    dataclass_replace(settings, enabled=bool(enabled))
                )
                changed = True
        if vision_provider is not None or vision_model is not None:
            current = resolve_vision_selection(root)
            provider = (
                current.provider
                if vision_provider is None
                else str(vision_provider or "").strip()
            )
            model = (
                current.model
                if vision_model is None
                else str(vision_model or "").strip()
            )
            if provider:
                saved = provider_config.list_providers(root)
                match = next(
                    (
                        item
                        for item in saved
                        if item.id == provider or item.name == provider
                    ),
                    None,
                )
                if match is None:
                    raise BackendError(
                        "provider_not_found",
                        f"proveedor de visión no encontrado: {provider!r}",
                        422,
                    )
                provider = match.id
                if not model:
                    model = str(match.model or "").strip()
            else:
                model = ""
            save_selection(root, provider=provider, model=model)
            changed = True
        payload = self.desktop_control(project_root)
        if not changed:
            return payload
        error = self._rebuild_runtime(project_root)
        return {**payload, "runtime_error": error} if error else payload

    # --- Safe Proxy embebido: ciclo de vida (arranca/para el servicio en proceso) ---

    def _safe_proxy(self):
        """Gestor del servicio embebido (uno por backend), creado a la primera."""
        if self._safe_proxy_manager is None:
            from satellite.safe_proxy.manager import SafeProxyManager

            self._safe_proxy_manager = SafeProxyManager(
                upstream_resolver=self._safe_proxy_upstream
            )
        return self._safe_proxy_manager

    def _safe_proxy_upstream(self) -> dict:
        """Credenciales del proveedor activo para el upstream (uso interno, sin exponer).

        El proxy reenvía al mismo proveedor con el que corre Satellite; si no hay uno
        configurado, el servicio usa su default y lo dice en su propia config redactada.
        """
        try:
            from satellite.config import providers as provider_config

            conn = provider_config.active_connection(self.resolve())
        except Exception:  # noqa: BLE001 — sin proveedor: se usan los defaults del servicio
            return {}
        base_url = str(conn.get("base_url") or "").strip()
        api_key = str(conn.get("api_key") or "")
        if not base_url.startswith("http"):
            return {"api_key": api_key} if api_key else {}
        return {"base_url": base_url, "api_key": api_key}

    def safe_proxy(self) -> dict:
        """Estado real del servicio embebido: stopped/running/config-error/port-conflict.

        Nunca devuelve los tokens (sólo ``control_token_set``); la interfaz ofrece
        start/stop y configuración, no una instrucción de lanzarlo a mano.
        """
        return self._safe_proxy().status()

    def start_safe_proxy(self) -> dict:
        """Arranca el servicio embebido en loopback (idempotente)."""
        return self._safe_proxy().start()

    def stop_safe_proxy(self) -> dict:
        """Para el servicio embebido y libera el puerto."""
        return self._safe_proxy().stop()

    def configure_safe_proxy(self, *, host: object = None, port: object = None) -> dict:
        """Cambia dónde escucha el servicio embebido (host loopback + puerto válido)."""
        from satellite.safe_proxy.config import SafeProxyConfigError

        try:
            return self._safe_proxy().configure(host=host, port=port)
        except SafeProxyConfigError as exc:
            raise BackendError(exc.code, exc.message, 422) from exc

    def set_safe_proxy_mode(self, mode: str) -> dict:
        """Cambia de modo: ``internal`` (sin listener) o ``server`` (loopback, start/stop)."""
        from satellite.safe_proxy.config import SafeProxyConfigError

        try:
            return self._safe_proxy().set_mode(mode)
        except SafeProxyConfigError as exc:
            raise BackendError(exc.code, exc.message, 422) from exc

    def patch_safe_proxy_config(self, changes: dict) -> dict:
        """Valida la allowlist y la aplica en el servicio vivo (``PATCH /control/config``)."""
        from satellite.safe_proxy.client import SafeProxyError
        from satellite.safe_proxy.config import SafeProxyConfigError, validate_config_patch

        try:
            body = validate_config_patch(changes or {})
        except SafeProxyConfigError as exc:
            raise BackendError(exc.code, exc.message, 422) from exc
        if not body:
            raise BackendError("safe_proxy_no_changes", "no se proporcionó ningún cambio válido", 422)
        try:
            return self._safe_proxy().patch(body)
        except SafeProxyError as exc:
            raise BackendError(exc.code, exc.message, exc.status or 502) from exc

    def set_safe_proxy_enabled(self, enabled: bool) -> dict:
        """Habilita o deshabilita el routing real del servicio (``/control/enable|disable``)."""
        from satellite.safe_proxy.client import SafeProxyError

        try:
            return self._safe_proxy().set_enabled(bool(enabled))
        except SafeProxyError as exc:
            raise BackendError(exc.code, exc.message, exc.status or 502) from exc

    # --- proveedores: vista con capacidades (esfuerzo), sin exponer la clave ---

    def _capability_overrides(self, root, provider_id: str = "") -> dict:
        """Capacidades por modelo: ``llm.models`` + las del proveedor indicado."""
        from satellite.config import providers as pconfig

        data = pconfig.runtime_config(root)
        llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
        merged: dict = {}
        source = llm.get("models")
        if isinstance(source, dict):
            merged.update(source)
        raw = data.get("providers") if isinstance(data.get("providers"), list) else []
        entry = (
            next(
                (p for p in raw if isinstance(p, dict) and str(p.get("id")) == str(provider_id)),
                None,
            )
            if provider_id
            else None
        )
        if isinstance(entry, dict) and isinstance(entry.get("models"), dict):
            merged.update(entry["models"])
        return merged

    def _effort_options_for(
        self,
        model: str,
        root,
        provider_id: str = "",
        announced: dict | None = None,
        family: str = "",
    ) -> tuple[str, ...]:
        """Niveles de esfuerzo que admite ese modelo (vacío = el control se desactiva).

        Los niveles salen del catálogo de capacidades (tabla por prefijo + ``llm.models``) y
        de lo que anuncie el proveedor; nunca se inventan. Un modelo del que no se sabe nada
        no declara niveles, así que la UI no ofrece el control (no se afirma soporte).
        """
        from satellite.providers.capabilities import effort_options

        return effort_options(model, self._capability_overrides(root, provider_id), announced)

    def _effort_meta_for(
        self, model: str, root, provider_id: str = "", announced: dict | None = None
    ) -> dict:
        """Metadata presentable del esfuerzo (niveles, control, origen, si es inferido)."""
        from satellite.providers.capabilities import effort_metadata

        return effort_metadata(model, self._capability_overrides(root, provider_id), announced)

    def _models_effort_options(
        self, modelos: list, capabilities: object, root, provider_id: str = "", family: str = ""
    ) -> dict:
        """Niveles ofrecibles por modelo: catálogo de capacidades + lo que anuncie la API."""
        anunciado = capabilities if isinstance(capabilities, dict) else {}
        out: dict[str, list[str]] = {}
        for model in modelos:
            mid = str(model)
            info = anunciado.get(mid)
            effort = info.get("effort") if isinstance(info, dict) else None
            opciones = self._effort_options_for(mid, root, provider_id, effort, family)
            if opciones:
                out[mid] = list(opciones)
        return out

    def _models_effort_meta(
        self, modelos: list, capabilities: object, root, provider_id: str = ""
    ) -> dict:
        """Metadata de esfuerzo por modelo (control, default, origen, inferido)."""
        anunciado = capabilities if isinstance(capabilities, dict) else {}
        out: dict[str, dict] = {}
        for model in modelos:
            mid = str(model)
            info = anunciado.get(mid)
            effort = info.get("effort") if isinstance(info, dict) else None
            meta = self._effort_meta_for(mid, root, provider_id, effort)
            if meta.get("options") or meta.get("control"):
                out[mid] = meta
        return out

    def _providers_payload(self, root) -> list[dict]:
        """Proveedores guardados + los niveles de esfuerzo que admite su modelo.

        La clave nunca sale: sólo ``api_key_set``. Los niveles salen del catálogo de
        capacidades, no de una autodetección inventada.
        """
        from satellite.config import providers as pconfig

        out: list[dict] = []
        for item in pconfig.list_providers(root):
            data = to_jsonable(item)
            if isinstance(data, dict):
                model = str(getattr(item, "model", ""))
                provider_id = str(getattr(item, "id", ""))
                data["effort_options"] = list(self._effort_options_for(model, root, provider_id))
                data["effort_meta"] = self._effort_meta_for(model, root, provider_id)
            out.append(data)
        return out

    def _effort_announced(
        self,
        effort_levels: list[str] | None,
        effort_control: str,
        effort_can_disable: bool = False,
    ) -> dict:
        """Anuncio de esfuerzo del endpoint, tal como lo manda la app (``{}`` si no hay).

        Se usa para dos cosas: validar contra los niveles que el endpoint declaró y
        persistirlos como capacidades del modelo (para que el adapter envíe el parámetro
        correcto también fuera de un rol).
        """
        niveles = [str(n).strip() for n in (effort_levels or []) if str(n).strip()]
        control = str(effort_control or "").strip()
        if not niveles and not control and not effort_can_disable:
            return {}
        anunciado: dict = {}
        if niveles:
            anunciado["supported_levels"] = niveles
        if control:
            anunciado["control"] = control
        if effort_can_disable:
            anunciado["can_disable"] = True
        return anunciado

    def _effort_model_overrides(self, model: str, announced: dict) -> dict:
        """Capacidades de esfuerzo por modelo para guardar en el proveedor (``{}`` si no hay)."""
        if not model or not announced:
            return {}
        from satellite.providers.capabilities import (
            EFFORT_PARAM,
            OFF_LEVELS,
            param_for_control,
        )

        niveles = [
            str(n).strip().lower()
            for n in announced.get("supported_levels", [])
            if str(n).strip()
        ]
        off = next((n for n in niveles if n in OFF_LEVELS), "")
        resto = [n for n in niveles if n not in OFF_LEVELS]
        entry: dict = {}
        if resto:
            entry["effort_levels"] = resto
        if off:
            entry["effort_off_value"] = off
        elif announced.get("can_disable"):
            entry["effort_off_value"] = OFF_LEVELS[0]
        control = str(announced.get("control") or "").strip()
        if control:
            entry["effort_control"] = control
            entry["effort_param"] = param_for_control(control) or EFFORT_PARAM
        think_param = str(announced.get("think_param") or "").strip()
        if think_param:
            entry["think_param"] = think_param
            if announced.get("think_on"):
                entry["think_on"] = str(announced["think_on"])
            if announced.get("think_off"):
                entry["think_off"] = str(announced["think_off"])
        return {model: entry} if entry else {}

    def _validate_effort(
        self, model: str, root, provider_id: str, value: str, announced: dict
    ) -> str:
        """Valida el nivel elegido contra lo que admite el modelo (``""`` = auto).

        No se persiste un valor que el modelo no admita: se comprueba contra el catálogo de
        capacidades más lo que anunció el endpoint (que viaja en ``announced``).
        """
        options = self._effort_options_for(model, root, provider_id, announced or None)
        chosen = (value or "").strip().lower()
        if chosen and chosen not in options:
            detalle = f" (admite: {', '.join(options)})" if options else " (no admite ninguno)"
            raise BackendError(
                "invalid_effort",
                f"el modelo {model!r} no admite el nivel {chosen!r}{detalle}",
                422,
            )
        return "" if chosen == "auto" else chosen

    def _validate_think(
        self, model: str, root, provider_id: str, value: bool | None, announced: dict
    ) -> bool | None:
        """Valida el interruptor Think contra el canal que declara el modelo.

        ``None`` = no tocar. Un booleano sólo se acepta si el modelo declara un canal Think
        (tabla/capacidades o config explícita); si no, se rechaza con motivo en vez de
        guardar un ajuste que el adapter nunca enviaría.
        """
        if value is None:
            return None
        meta = self._effort_meta_for(model, root, provider_id, announced or None)
        if not meta.get("think_param"):
            raise BackendError(
                "invalid_think",
                f"el modelo {model!r} no declara un canal Think; el control queda deshabilitado",
                422,
            )
        return bool(value)

    def patch_provider(
        self,
        provider_id: str,
        *,
        name: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str = "",
        api_key_env: str | None = None,
        account_id: str | None = None,
        reasoning_effort: str | None = None,
        think: bool | None = None,
        max_tokens: int | None = None,
        effort_levels: list[str] | None = None,
        effort_control: str = "",
        effort_can_disable: bool = False,
        project_root=None,
    ) -> dict:
        """Actualiza un proveedor guardado (por ``id``) y rearma el runtime.

        Sólo toca lo que llega; el esfuerzo se valida contra lo que el modelo admite (con los
        niveles anunciados que mande la app) y sus capacidades se guardan con el proveedor.
        """
        from satellite.config import providers as pconfig

        root = self.resolve(project_root)
        provider = pconfig.find_provider(root, provider_id)
        if provider is None:
            raise BackendError(
                "provider_not_found", f"no existe el proveedor {provider_id!r}", 404
            )
        modelo = (model if model is not None else provider.model) or provider.model
        announced = self._effort_announced(effort_levels, effort_control, effort_can_disable)
        effort: str | None = None
        overrides: dict | None = None
        if reasoning_effort is not None:
            effort = self._validate_effort(modelo, root, provider.id, reasoning_effort, announced)
            overrides = self._effort_model_overrides(modelo, announced)
        pconfig.update_provider(
            root,
            provider.id,
            name=name,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_env,
            account_id=account_id,
            reasoning_effort=effort,
            think=self._validate_think(modelo, root, provider.id, think, announced),
            max_tokens=max_tokens,
            model_overrides=overrides,
        )
        error = self._rebuild_runtime(project_root)
        payload = self.config(project_root)
        return {**payload, "runtime_error": error} if error else payload

    def update_config(
        self, *, policy_mode: str = "", safe_mode: bool | None = None, project_root=None
    ) -> dict:
        session = self.session(project_root)
        changed = False
        if policy_mode:
            mode = policy_mode.strip().lower()
            if mode not in ("ask", "auto", "unrestricted"):
                raise BackendError(
                    "invalid_policy", f"modo de política desconocido: {policy_mode!r}", 422
                )
            session.set_mode(mode)
            changed = True
        if safe_mode is not None:
            session.set_safe_mode(bool(safe_mode))
            changed = True
        payload = self.config(project_root)
        if not changed:
            return payload
        # El modo entra en cómo se arma el runtime (política del expansor) y el modo seguro
        # cambia el catálogo: se rearma y se vuelve a enganchar el puente de eventos.
        error = self._rebuild_runtime(project_root)
        return {**payload, "runtime_error": error} if error else payload

    def save_provider(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str = "",
        api_key_env: str = "",
        account_id: str = "",
        reasoning_effort: str = "",
        think: bool | None = None,
        max_tokens: int = 0,
        effort_levels: list[str] | None = None,
        effort_control: str = "",
        effort_can_disable: bool = False,
        project_root=None,
    ) -> dict:
        session = self.session(project_root)
        if not name.strip() or not model.strip() or not base_url.strip():
            raise BackendError(
                "invalid_provider", "nombre, modelo y URL son obligatorios", 422
            )
        root = self.resolve(project_root)
        announced = self._effort_announced(effort_levels, effort_control, effort_can_disable)
        effort = self._validate_effort(model.strip(), root, "", reasoning_effort, announced)
        overrides = self._effort_model_overrides(model.strip(), announced)
        session.save_provider(
            name=name.strip(),
            model=model.strip(),
            base_url=base_url.strip(),
            api_key=api_key,
            api_key_env=api_key_env,
            account_id=account_id.strip(),
            reasoning_effort=effort,
            think=self._validate_think(model.strip(), root, "", think, announced),
            max_tokens=max_tokens,
            model_overrides=overrides,
        )
        payload = self.config(project_root)
        error = self._rebuild_runtime(project_root)
        return {**payload, "runtime_error": error} if error else payload

    def models(
        self,
        *,
        base_url: str = "",
        family: str = "openai",
        api_key: str = "",
        provider: str = "",
        project_root=None,
    ) -> dict:
        """Lista los modelos de esa URL y sugiere el más usado, como el asistente del CLI.

        Con ``provider`` (id guardado) se usan sus credenciales del **servidor**: la clave
        nunca viaja a la interfaz. Sin él funciona como antes (URL + familia + clave).
        """
        from satellite.config import catalog as provider_catalog
        from satellite.config.models import default_model

        provider_key = ""
        account_id = ""
        if provider:
            from satellite.config import providers as pconfig

            conn = pconfig.provider_connection(self.resolve(project_root), provider)
            if conn is None:
                raise BackendError(
                    "provider_not_found", f"no existe el proveedor {provider!r}", 404
                )
            # Ruta por proveedor guardado: su endpoint, su familia y su clave, sin mezclar
            # nada con lo que pueda venir del formulario.
            base_url = str(conn.get("base_url") or "")
            family = str(conn.get("family") or "openai")
            api_key = str(conn.get("api_key") or "")
            provider_key = str(conn.get("provider") or "")
            account_id = str(conn.get("account_id") or "")
            if not base_url.strip():
                raise BackendError(
                    "invalid_request", f"el proveedor {provider!r} no tiene URL base", 422
                )
        if not base_url.strip():
            raise BackendError("invalid_request", "falta 'base_url' o 'provider'", 422)
        # Sin id guardado, se reconoce el proveedor por su URL (catálogo) para usar su
        # estrategia de listado (p. ej. xAI /language-models) en vez de asumir /models.
        if not provider_key:
            provider_key = provider_catalog.key_for_base_url(base_url.strip())
        root = self.resolve(project_root)
        session = self.session(project_root)
        result = session.load_models(
            base_url.strip(), family or "openai", api_key, provider_key, account_id
        )
        data = to_jsonable(result)
        if isinstance(data, dict):
            modelos = data.get("models") if isinstance(data.get("models"), list) else []
            data["suggested"] = default_model([str(item) for item in modelos]) if modelos else ""
            # Niveles de esfuerzo por modelo: catálogo de capacidades + lo anunciado por la API.
            data["effort_options"] = self._models_effort_options(
                modelos, data.get("capabilities"), root, provider, family
            )
            data["effort_meta"] = self._models_effort_meta(
                modelos, data.get("capabilities"), root, provider
            )
        return data

    # --- cierre ---

    async def close(self) -> None:
        """Cierra ordenadamente: corta los flujos, para el proxy y espera las escrituras."""
        if self._stop is not None:
            self._stop.set()
        # El servicio embebido se para aquí: al salir no queda nada escuchando.
        if self._safe_proxy_manager is not None:
            try:
                self._safe_proxy_manager.stop()
            except Exception:  # noqa: BLE001 — el cierre no debe fallar por esto
                pass
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

    def commands(self) -> dict:
        """Catálogo completo y su paridad (una definición, tres interfaces).

        Se devuelve **todo** el catálogo, no sólo la superficie de escritorio: así la app
        filtra por ``surfaces`` y puede además mostrar los huecos de paridad.
        """
        from satellite.commands import CATALOG, parity

        return {
            "commands": [spec.to_dict() for spec in CATALOG],
            "parity": parity(),
        }

    def set_active_provider(self, provider_id: str, project_root=None) -> dict:
        """Fija el proveedor global guardado y rearma el runtime (no inventa uno nuevo).

        Sigue el criterio de ``activate``: los roles que apuntaban al global le siguen y
        los fijados a otro proveedor se respetan (sin cascada silenciosa).
        """
        from satellite.config import providers as pconfig

        root = self.resolve(project_root)
        if pconfig.find_provider(root, provider_id) is None:
            raise BackendError(
                "provider_not_found", f"no existe el proveedor {provider_id!r}", 404
            )
        try:
            pconfig.activate(root, provider_id)
        except KeyError as exc:  # noqa: BLE001 — mismo caso: no existe
            raise BackendError("provider_not_found", str(exc), 404) from exc
        error = self._rebuild_runtime(project_root)
        payload = self.config(project_root)
        return {**payload, "runtime_error": error} if error else payload

    def roles(self, project_root=None) -> dict:
        """Roles configurados por separado, y a qué proveedor apunta cada uno."""
        from satellite.config import providers as pconfig

        root = self.resolve(project_root)
        return {
            "roles": list(pconfig.ROLES),
            "configured": to_jsonable(pconfig.role_config(root)),
            "active": pconfig.active_id(root),
            "providers": self._providers_payload(root),
        }

    def apply_role(
        self,
        role: str,
        provider_id: str,
        model: str = "",
        reasoning_effort: str = "",
        think: bool | None = None,
        effort_levels: list[str] | None = None,
        effort_control: str = "",
        project_root=None,
    ) -> dict:
        """Fija un rol a un proveedor guardado. Sin cascada: sólo ese rol.

        El esfuerzo se guarda **en el rol** (``llm.<rol>.reasoning_effort``) y no toca a los
        demás ni al global. Los niveles que el endpoint anunció para ese modelo viajan en
        ``effort_levels`` (y su canal en ``effort_control``) y se guardan con el rol, para que
        el adapter pueda enviarlos con el parámetro correcto.
        """
        from satellite.config import providers as pconfig

        root = self.resolve(project_root)
        if role not in pconfig.ROLES:
            raise BackendError(
                "invalid_role", f"rol desconocido: {role!r} (roles: {', '.join(pconfig.ROLES)})", 422
            )
        if not provider_id:
            pconfig.clear_roles(root, [role])
        else:
            proveedor = pconfig.find_provider(root, provider_id)
            if proveedor is None:
                raise BackendError(
                    "provider_not_found", f"no existe el proveedor {provider_id!r}", 404
                )
            anunciados = [str(nivel) for nivel in (effort_levels or []) if str(nivel).strip()]
            control = str(effort_control or "").strip()
            anunciado: dict = {}
            if anunciados:
                anunciado["supported_levels"] = anunciados
            if control:
                anunciado["control"] = control
            modelo = model.strip() or proveedor.model
            niveles = self._effort_options_for(modelo, root, provider_id, anunciado or None)
            esfuerzo = (reasoning_effort or "").strip().lower()
            if esfuerzo and esfuerzo not in niveles:
                detalle = f" (admite: {', '.join(niveles)})" if niveles else " (no admite ninguno)"
                raise BackendError(
                    "invalid_effort",
                    f"el modelo {modelo!r} no admite el nivel {esfuerzo!r}{detalle}",
                    422,
                )
            section: dict = {"provider": provider_id}
            if model.strip():
                section["model"] = model.strip()
            if esfuerzo and esfuerzo != "auto":
                section["reasoning_effort"] = esfuerzo
            think_value = self._validate_think(
                modelo, root, provider_id, think, anunciado or None
            )
            if think_value is not None:
                section["think"] = think_value
            if anunciados:
                section["effort_levels"] = anunciados
            if control:
                section["effort_control"] = control
            pconfig.set_roles(root, {role: section})
        # La configuración de rol tiene que cambiar lo que de verdad se ejecuta, no quedarse
        # escrita esperando a un reinicio: se reconstruye el runtime en el sitio.
        error = self._rebuild_runtime(project_root)
        payload = self.roles(project_root)
        return {**payload, "runtime_error": error} if error else payload

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

    def tools(self, project_root=None) -> list[dict]:
        """Herramientas del runtime, marcando las primitivas del kernel (``tools list``)."""
        from satellite.cli.runtime_builder import build_tool_registry
        from satellite.kernel.manifest import is_reserved

        out: list[dict] = []
        for tool in build_tool_registry().all():
            out.append(
                {
                    "id": tool.id,
                    "categories": [str(item) for item in (getattr(tool, "categories", ()) or ())],
                    "description": str(getattr(tool, "description", "")),
                    "reserved": bool(is_reserved(tool.id)),
                }
            )
        return out

    def context_usage(self, project_root=None) -> dict:
        """Uso de contexto detallado (lo que muestra ``satellite context``)."""
        from satellite.context.compaction import format_usage

        usage = self.session(project_root).context_usage()
        return {
            "text": format_usage(usage),
            "percent": int(getattr(usage, "percent", 0) or 0),
            "used_tokens": int(getattr(usage, "used_tokens", 0) or 0),
            "real_tokens": int(getattr(usage, "real_tokens", 0) or 0),
            "estimated_tokens": int(getattr(usage, "estimated_tokens", 0) or 0),
            "window_tokens": int(getattr(usage, "window_tokens", 0) or 0),
            "window_source": str(getattr(usage, "window_source", "")),
        }

    # --- @menciones del compositor (contexto bajo demanda) ---

    def _mention_candidates(self, project_root=None) -> list[dict]:
        """Rutas del proyecto para el autocompletado de @menciones (caché corta por proyecto).

        Reutiliza el mismo ``ProjectIndex`` con el que el ContextEngine resuelve referencias:
        no inventa un listado propio ni indexa nada al arrancar (sólo recorre cuando se pide).
        """
        import time

        from satellite.context.project_index import ProjectIndex

        root = self.resolve(project_root)
        key = str(root)
        cached = self._index_cache.get(key)
        ahora = time.monotonic()
        if cached is not None and ahora - cached[0] < MENTION_CACHE_S:
            return cached[1]
        vistos: set[str] = set()
        items: list[dict] = []
        for path in ProjectIndex(root).list_project_files():
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            partes = rel.split("/")
            # El archivo y sus carpetas padre: la TUI también acepta «@dir».
            for corte in range(1, len(partes)):
                carpeta = "/".join(partes[:corte])
                if carpeta not in vistos:
                    vistos.add(carpeta)
                    items.append({"value": carpeta, "kind": "dir"})
            if rel not in vistos:
                vistos.add(rel)
                items.append({"value": rel, "kind": "path"})
        items.sort(key=lambda item: item["value"])
        self._index_cache[key] = (ahora, items)
        return items

    def mention_suggestions(
        self, query: str, *, limit: int = MENTION_LIMIT, project_root=None
    ) -> list[dict]:
        """Candidatos para una @mención: primero las rutas que empiezan por lo escrito."""
        texto = (query or "").strip().lower()
        candidatos = self._mention_candidates(project_root)
        if not texto:
            return candidatos[:limit]
        empiezan = [item for item in candidatos if item["value"].lower().startswith(texto)]
        contienen = [
            item
            for item in candidatos
            if texto in item["value"].lower() and not item["value"].lower().startswith(texto)
        ]
        return (empiezan + contienen)[:limit]

    def _mention_context(self, text: str, project_root=None) -> dict:
        """Qué @menciones del mensaje se adjuntan y cuáles no existen (igual que la TUI)."""
        from satellite.context.mentions import parse as parse_mentions

        menciones = parse_mentions(text, self.resolve(project_root))
        return {
            "refs": [{"value": ref.value, "kind": ref.kind.value} for ref in menciones.refs],
            "missing": list(menciones.missing),
            "summary": menciones.summary(),
        }

    def initialize(self, project_root=None) -> dict:
        """Inicializa o repara el proyecto: crea ``.satellite/`` y aplica el esquema (``init``)."""
        root = self.resolve(project_root)
        self.session(project_root).paths()
        FileStore(root).migrate()
        from satellite.context.project_index import ProjectIndex

        ProjectIndex(root).ensure_persisted()
        return self.status(project_root)

    def remove_provider(self, provider_id: str, project_root=None) -> dict:
        """Elimina un proveedor guardado (``provider remove``); los demás no se tocan."""
        from satellite.config import providers as pconfig

        root = self.resolve(project_root)
        if not pconfig.delete_provider(root, provider_id):
            raise BackendError(
                "provider_not_found", f"no existe el proveedor {provider_id!r}", 404
            )
        error = self._rebuild_runtime(project_root)
        payload = {**self.config(project_root), "removed": provider_id}
        return {**payload, "runtime_error": error} if error else payload

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
        self._require_project()
        self.require_runtime(project_root)
        outcome = await self.session(project_root).curate(
            request, approval=self._approval_for(project_root)
        )
        return to_jsonable(outcome)

    async def create_agent(self, need: str, project_root=None) -> dict:
        self._require_project()
        self.require_runtime(project_root)
        outcome = await self.session(project_root).create_agent(
            need, approval=self._approval_for(project_root)
        )
        return to_jsonable(outcome)
