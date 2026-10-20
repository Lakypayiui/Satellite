"""Sesiones de conversación: crear, nombrar, listar y reanudar.

Antes había una sola conversación por proyecto (``state/conversation.json``). Ahora cada
sesión vive en ``state/conversations/<id>.json`` y ``state/current_session.json`` dice cuál
está activa. La conversación que ya existía se adopta como sesión ``default`` la primera
vez, así que nadie pierde su historial.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from satellite.core.atomic import atomic_write_text

LEGACY_FILE = "conversation.json"
SESSION_DIR = "conversations"
CURRENT_FILE = "current_session.json"
DEFAULT_SESSION_ID = "default"
DEFAULT_SESSION_NAME = "conversación principal"
NEW_SESSION_NAME = "nueva conversación"

# Palabras vacías (ES/EN) para el título automático de 2-3 palabras. No es un
# resumen semántico: evita que el nombre sea "Quiero que me hagas un…".
_TITLE_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "al", "algo", "an", "and", "are", "as", "con", "como", "cómo", "de",
        "del", "desde", "donde", "dónde", "el", "ella", "ellas", "ellos", "en",
        "entre", "era", "es", "esa", "ese", "eso", "esta", "este", "esto", "estos",
        "estas", "for", "fue", "ha", "hacer", "haz", "hagas", "has", "hasta", "hay",
        "la", "las", "le", "les", "lo", "los", "me", "mi", "mis", "muy", "no", "nos",
        "o", "of", "on", "or", "para", "pero", "please", "por", "puede", "puedes",
        "que", "qué", "quiero", "se", "ser", "si", "sí", "sin", "sobre", "son", "su",
        "sus", "te", "the", "this", "that", "to", "tu", "tus", "un", "una", "uno",
        "unos", "unas", "usted", "ustedes", "y", "ya", "you", "your", "can",
        "could", "would", "should", "what", "which", "how", "why", "with", "from",
        "into", "will", "just", "like", "make", "want", "need", "necesito",
        "abre", "abrir", "entra", "entrar", "navega", "navegar", "busca", "buscar",
        "crea", "crear", "haz", "hacer", "funciona", "funcionar",
    }
)
_WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_.+-]+")


def summarize_session_name(text: str, *, max_words: int = 3) -> str:
    """Título corto (2-3 palabras) a partir del primer mensaje, sin LLM."""
    raw = unicodedata.normalize("NFC", str(text or ""))
    words = _WORD_RE.findall(raw)
    meaningful = [
        word for word in words if word.lower().strip("._-") not in _TITLE_STOPWORDS
    ]
    chosen = (meaningful or words)[:max_words]
    if not chosen:
        return ""
    title = " ".join(chosen)
    title = title.strip(" .,:;!?¡¿-–—")
    if not title:
        return ""
    return title[0].upper() + title[1:]


@dataclass(frozen=True)
class SessionInfo:
    """Lo que se muestra de una sesión en la lista.

    ``pinned`` y ``archived`` son estados persistentes. Las sesiones de antes de que
    existieran no traen las claves: se leen como ``False`` (compatibilidad).
    """

    id: str
    name: str
    turns: int = 0
    updated_at: str = ""
    active: bool = False
    pinned: bool = False
    archived: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Ficheros de sesiones de conversación de un proyecto."""

    def __init__(self, project_root: Path | str) -> None:
        self.state = Path(project_root) / ".satellite" / "state"

    # --- rutas ---

    def session_path(self, session_id: str) -> Path:
        return self.state / SESSION_DIR / f"{session_id}.json"

    def current_path(self) -> Path:
        """Fichero de la sesión activa (adopta la conversación antigua si hacía falta)."""
        session_id = self.active_id()
        path = self.session_path(session_id)
        legacy = self.state / LEGACY_FILE
        if session_id == DEFAULT_SESSION_ID and not path.exists() and legacy.is_file():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                legacy.replace(path)
            except OSError:
                return legacy
        return path

    # --- qué sesión está activa ---

    def active_id(self) -> str:
        try:
            data = json.loads((self.state / CURRENT_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DEFAULT_SESSION_ID
        if isinstance(data, dict) and isinstance(data.get("id"), str) and data["id"].strip():
            return data["id"].strip()
        return DEFAULT_SESSION_ID

    def set_active(self, session_id: str) -> None:
        try:
            self.state.mkdir(parents=True, exist_ok=True)
            (self.state / CURRENT_FILE).write_text(
                json.dumps({"id": session_id}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            return

    # --- leer / escribir ---

    def read(self, session_id: str) -> tuple[SessionInfo, list[dict]]:
        """``(info, turnos)`` de una sesión; vacío y tolerante si no se puede leer."""
        path = self.session_path(session_id)
        if not path.is_file() and session_id == DEFAULT_SESSION_ID:
            # la sesión principal existe aunque todavía no tenga fichero
            return SessionInfo(id=session_id, name=DEFAULT_SESSION_NAME), []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return SessionInfo(id=session_id, name=session_id), []
        if not isinstance(data, dict):
            return SessionInfo(id=session_id, name=session_id), []
        raw = data.get("turns") if isinstance(data.get("turns"), list) else []
        turns = [item for item in raw if isinstance(item, dict)]
        info = SessionInfo(
            id=session_id,
            name=str(data.get("name") or session_id),
            turns=len(turns),
            updated_at=str(data.get("updated_at") or ""),
            pinned=bool(data.get("pinned")),
            archived=bool(data.get("archived")),
        )
        return info, turns

    def save(
        self,
        session_id: str,
        turns: list[dict],
        *,
        name: str = "",
        pinned: bool | None = None,
        archived: bool | None = None,
        touch: bool = True,
    ) -> bool:
        """Escribe la sesión (atómico). Devuelve False si no se pudo guardar.

        ``pinned``/``archived`` en ``None`` conservan lo que ya hubiera (y ``False`` si la
        sesión es antigua). ``touch=False`` no actualiza ``updated_at``: fijar o archivar no
        es actividad de la conversación.
        """
        path = self.session_path(session_id)
        previous: dict = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    previous = raw
            except (OSError, ValueError):
                previous = {}
        payload = {
            "id": session_id,
            "name": name or str(previous.get("name") or session_id),
            "created_at": str(previous.get("created_at") or _now()),
            "updated_at": _now() if touch else str(previous.get("updated_at") or ""),
            "turns": turns,
            "pinned": bool(previous.get("pinned")) if pinned is None else bool(pinned),
            "archived": bool(previous.get("archived")) if archived is None else bool(archived),
        }
        try:
            atomic_write_text(
                path, json.dumps(payload, ensure_ascii=False, indent=2)
            )
        except OSError:
            return False
        return True

    # --- crear / listar / cambiar / renombrar ---

    def create(self, name: str = "") -> str:
        """Crea una sesión vacía, la activa y devuelve su id.

        Sin nombre explícito se usa un placeholder; el primer mensaje la renombra
        con un resumen de 2-3 palabras (``summarize_session_name``).
        """
        session_id = uuid4().hex[:8]
        self.save(session_id, [], name=name.strip() or NEW_SESSION_NAME)
        self.set_active(session_id)
        return session_id

    def delete(self, session_id: str) -> bool:
        """Borra una conversación y sus turnos. Si era la activa, activa otra."""
        target = str(session_id or "").strip()
        if not target:
            return False
        path = self.session_path(target)
        existed = path.is_file()
        if existed:
            try:
                path.unlink()
            except OSError:
                return False
        # La memoria persistente pertenece a la conversación: se borra con ella.
        summary = self.state / "summaries" / f"{target}.json"
        if summary.is_file():
            try:
                summary.unlink()
            except OSError:
                pass
        if self.active_id() == target:
            # ``list`` añade una sesión «default» sintética cuando la activa ya no
            # existe; no debe ganar a una conversación real que sí tiene fichero.
            candidatas = [
                info
                for info in self.list()
                if info.id != target
                and (
                    info.id != DEFAULT_SESSION_ID
                    or self.session_path(DEFAULT_SESSION_ID).is_file()
                )
            ]
            preferidas = [info for info in candidatas if not info.archived] or candidatas
            self.set_active(preferidas[0].id if preferidas else DEFAULT_SESSION_ID)
        # Borrar una sesión «default» vacía es un no-op válido.
        return existed or target != DEFAULT_SESSION_ID

    def list(self) -> list[SessionInfo]:
        """Sesiones conocidas: la activa primero y el resto por fecha."""
        active = self.active_id()
        infos: list[SessionInfo] = []
        directory = self.state / SESSION_DIR
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                info = self._info(path, active=path.stem == active)
                if info is not None:
                    infos.append(info)
        if not any(info.id == active for info in infos):
            infos.append(SessionInfo(DEFAULT_SESSION_ID, DEFAULT_SESSION_NAME, active=True))
        infos.sort(key=lambda info: info.updated_at, reverse=True)
        infos.sort(key=lambda info: not info.active)  # estable: la activa arriba
        return infos

    def find(self, target: str) -> str | None:
        """Id de la sesión a partir de su id o de su nombre (sin distinguir mayúsculas)."""
        wanted = (target or "").strip().lower()
        if not wanted:
            return None
        if wanted == DEFAULT_SESSION_ID or self.session_path(wanted).is_file():
            return wanted
        for info in self.list():
            if info.name.lower() == wanted:
                return info.id
        return None

    def switch(self, target: str) -> str | None:
        """Reanuda una sesión por id o nombre; None si no existe."""
        session_id = self.find(target)
        if session_id is None:
            return None
        self.set_active(session_id)
        return session_id

    def rename(self, session_id: str, name: str) -> bool:
        """Renombra la sesión (conserva sus turnos, su fijado y su archivo)."""
        _info, turns = self.read(session_id)
        return self.save(session_id, turns, name=name.strip())

    def set_pinned(self, session_id: str, pinned: bool) -> bool:
        """Fija o deja de fijar la sesión. No cambia cuál está activa."""
        info, turns = self.read(session_id)
        # Se reenvía el nombre leído: la sesión «default» puede no tener fichero todavía y su
        # nombre por defecto no está en disco (evita rebautizarla al fijar).
        return self.save(session_id, turns, name=info.name, pinned=bool(pinned), touch=False)

    def set_archived(self, session_id: str, archived: bool) -> bool:
        """Archiva o desarchiva la sesión (conserva sus turnos: archivar no borra nada)."""
        info, turns = self.read(session_id)
        return self.save(session_id, turns, name=info.name, archived=bool(archived), touch=False)

    def next_active_after_archive(self, archived_id: str) -> str | None:
        """Conversación sin archivar a la que pasar tras archivar la activa.

        Prefiere las fijadas y, dentro de cada grupo, la más reciente. Devuelve ``None`` si no
        queda ninguna otra sin archivar: entonces no hay conversación activa (estado vacío).
        Es la misma operación para la app, la CLI y la TUI.
        """
        candidatas = [
            info for info in self.list() if info.id != archived_id and not info.archived
        ]
        if not candidatas:
            return None
        candidatas.sort(key=lambda info: info.updated_at or "", reverse=True)  # recencia
        candidatas.sort(key=lambda info: not info.pinned)  # estable: las fijadas primero
        return candidatas[0].id

    def _info(self, path: Path, *, active: bool) -> SessionInfo | None:
        _info, turns = self.read(path.stem)
        if not path.is_file():
            return None
        return SessionInfo(
            id=path.stem,
            name=_info.name,
            turns=len(turns),
            updated_at=_info.updated_at,
            active=active,
            pinned=_info.pinned,
            archived=_info.archived,
        )
