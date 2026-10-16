"""Registro de lo que una tarea escribió, con copia de la versión anterior.

``git diff`` sólo ve ficheros rastreados y sólo si el proyecto es un repo, y los
snapshots que ya existían son del directorio de datos, no del proyecto. Para poder
mostrar «qué tocó esta tarea» y deshacerlo, cada escritura anota aquí lo que había
antes. La **primera** escritura de la tarea manda: restaurar deja el fichero como
estaba antes de que la tarea empezara.

Sólo se registran rutas dentro del proyecto: una ruta concedida fuera del proyecto no
entra en el registro, porque no habría forma portable de nombrarla.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from satellite.kernel.paths import resolve_paths

MAX_BACKUP_BYTES = 512_000
DIFF_CONTEXT_LINES = 3
MANIFEST_NAME = "manifest.json"


class ChangeLedger:
    """Cambios del proyecto atribuidos a una tarea."""

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root)
        data = resolve_paths(self.project_root).project_data
        self.root = Path(data or (self.project_root / ".satellite")) / "state" / "changes"
        self._base = self.project_root.resolve()

    def record(self, task_id: str, path: Path | str, previous: str | None) -> bool:
        """Anota que la tarea tocó ``path``, guardando lo que había antes.

        Devuelve ``False`` sólo si no se pudo guardar; la escritura ya ocurrió, así que
        el fallo se comunica como aviso y no como error.
        """
        if not task_id:
            return False
        try:
            relative = Path(path).resolve().relative_to(self._base).as_posix()
        except (OSError, ValueError):
            return True  # fuera del proyecto: no es un fallo, es que no aplica
        target = self.root / task_id
        manifest = self._manifest(target)
        if any(entry.get("path") == relative for entry in manifest):
            return True
        entry: dict = {"path": relative}
        if previous is None:
            entry["existed"] = False
        else:
            size = len(previous.encode("utf-8"))
            if size > MAX_BACKUP_BYTES:
                entry.update(existed=True, restorable=False, bytes=size)
            else:
                name = f"{len(manifest)}.bak"
                try:
                    (target / "backups").mkdir(parents=True, exist_ok=True)
                    (target / "backups" / name).write_text(previous, encoding="utf-8")
                except OSError:
                    return False
                entry.update(existed=True, restorable=True, backup=name)
        manifest.append(entry)
        return self._write_manifest(target, manifest)

    def entries(self, task_id: str) -> list[dict]:
        """Ficheros registrados para la tarea, en el orden en que se tocaron."""
        return self._manifest(self.root / task_id)

    def files(self, task_id: str) -> list[str]:
        return [str(entry.get("path", "")) for entry in self.entries(task_id)]

    def has(self, task_id: str) -> bool:
        return bool(self.entries(task_id))

    def latest_task(self) -> str | None:
        """La última tarea con cambios registrados (por fecha del manifiesto)."""
        if not self.root.is_dir():
            return None
        candidatos = [d for d in self.root.iterdir() if (d / MANIFEST_NAME).is_file()]
        if not candidatos:
            return None
        # el nombre desempata: dos tareas en el mismo tick de reloj dan el mismo mtime
        return max(
            candidatos, key=lambda d: ((d / MANIFEST_NAME).stat().st_mtime, d.name)
        ).name

    def diff(self, task_id: str, *, context: int = DIFF_CONTEXT_LINES) -> str:
        """Diff unificado de lo que la tarea cambió, contra la versión previa."""
        bloques: list[str] = []
        for entry in self.entries(task_id):
            name = str(entry.get("path", ""))
            if entry.get("existed") and not entry.get("restorable", True):
                bloques.append(
                    f"--- {name}\n(sin copia previa: {entry.get('bytes', '?')} bytes; "
                    "no se puede mostrar el diff ni deshacer este archivo)"
                )
                continue
            anterior = self._previous(task_id, entry)
            actual = ""
            path = self.project_root / name
            try:
                if path.is_file():
                    actual = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                actual = ""
            texto = "".join(
                difflib.unified_diff(
                    anterior.splitlines(keepends=True),
                    actual.splitlines(keepends=True),
                    fromfile=("/dev/null" if not anterior else f"a/{name}"),
                    tofile=f"b/{name}",
                    n=context,
                )
            )
            bloques.append(texto or f"--- {name}\n(sin cambios)")
        return "".join(bloques)

    def restore(self, task_id: str) -> dict:
        """Devuelve los archivos de esa tarea al estado previo a la tarea."""
        resultado: dict[str, list[str]] = {"restored": [], "removed": [], "skipped": []}
        for entry in self.entries(task_id):
            name = str(entry.get("path", ""))
            path = self.project_root / name
            if not self._inside(path):
                resultado["skipped"].append(f"{name} (fuera del proyecto)")
                continue
            if not entry.get("existed"):
                try:
                    if path.is_file():
                        path.unlink()
                        resultado["removed"].append(name)
                    else:
                        resultado["skipped"].append(f"{name} (ya no estaba)")
                except OSError as exc:
                    resultado["skipped"].append(f"{name} ({exc})")
                continue
            if not entry.get("restorable", True):
                resultado["skipped"].append(f"{name} (sin copia previa)")
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self._previous(task_id, entry), encoding="utf-8")
                resultado["restored"].append(name)
            except OSError as exc:
                resultado["skipped"].append(f"{name} ({exc})")
        return resultado

    def _inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._base)
        except (OSError, ValueError):
            return False
        return True

    def _previous(self, task_id: str, entry: dict) -> str:
        name = entry.get("backup")
        if not name:
            return ""
        try:
            return (self.root / task_id / "backups" / str(name)).read_text(
                encoding="utf-8"
            )
        except OSError:
            return ""

    def _manifest(self, target: Path) -> list[dict]:
        try:
            data = json.loads((target / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict)]

    def _write_manifest(self, target: Path, manifest: list[dict]) -> bool:
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            return False
        return True
