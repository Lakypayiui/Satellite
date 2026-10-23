"""Registro de skills en disco: carga tolerante, dedup, cuarentena y stats."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from satellite.core.events import EventType, ExecutionEvent
from satellite.core.errors import SpecValidationError, ToolError
from satellite.events.base import EventSink
from satellite.kernel.manifest import is_reserved, sha256_bytes
from satellite.retrieval.base import Hit
from satellite.retrieval.index import DEFAULT_MIN_SCORE, SkillIndex
from satellite.skills.audit import code_digest
from satellite.skills.models import SkillManifest, SkillStats, SkillStatus
from satellite.tool_manifest import is_tool_id

MANIFEST_NAME = "manifest.json"
QUARANTINE_DIR = "_quarantine"
HISTORY_DIR = "_history"
SYSTEM_TASK_ID = "system"
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


class SkillRegistry:
    """Catálogo de skills respaldado por archivos bajo ``skills_root``.

    La carga es tolerante: un manifest o script corrupto se mueve a cuarentena
    y se emite un evento; nunca impide el arranque.
    """

    def __init__(
        self,
        root: Path,
        *,
        index: SkillIndex | None = None,
        sink: EventSink | None = None,
        max_script_bytes: int = 200_000,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        self.root = Path(root)
        self.index = index or SkillIndex()
        self.sink = sink
        self.max_script_bytes = max_script_bytes
        self.max_consecutive_failures = max_consecutive_failures
        self._manifests: dict[str, SkillManifest] = {}

    # --- carga ---

    def load_all(self) -> list[SkillManifest]:
        """Carga todas las skills del directorio (tolerante a corrupción)."""
        self._manifests.clear()
        if not self.root.exists():
            self.index.rebuild_skills([])
            return []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            manifest = self._load_one(entry)
            if manifest is not None:
                self._manifests[manifest.id] = manifest
        self.index.rebuild_skills(self._manifests.values())
        return list(self._manifests.values())

    def _load_one(self, skill_dir: Path) -> SkillManifest | None:
        try:
            raw = json.loads((skill_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
            manifest = SkillManifest.from_dict(raw)
        except (OSError, json.JSONDecodeError, SpecValidationError, KeyError, ValueError):
            self.quarantine_dir(skill_dir, "manifest inválido")
            return None
        if not (skill_dir / manifest.entrypoint).exists():
            self.quarantine_dir(skill_dir, "entrypoint ausente")
            return None
        return manifest

    # --- consulta ---

    def get(self, skill_id: str) -> SkillManifest:
        """Devuelve el manifest o lanza ``ToolError`` si no existe."""
        if skill_id not in self._manifests:
            raise ToolError(f"skill no registrada: '{skill_id}'")
        return self._manifests[skill_id]

    def all(self) -> list[SkillManifest]:
        """Todos los manifests cargados."""
        return list(self._manifests.values())

    def count(self) -> int:
        """Número de skills registradas."""
        return len(self._manifests)

    def find_similar(
        self,
        description: str,
        *,
        top_k: int = 3,
        min_score: float | None = DEFAULT_MIN_SCORE,
    ) -> list[Hit]:
        """Busca skills parecidas (para dedup antes de crear)."""
        return self.index.search(description, top_k=top_k, min_score=min_score)

    def has_similar(self, description: str) -> bool:
        """True si existe una skill suficientemente parecida."""
        return bool(self.find_similar(description, top_k=1))

    # --- mutación ---

    def register(
        self,
        manifest: SkillManifest,
        script: str,
        *,
        actor: str = "",
    ) -> SkillManifest:
        """Registra (o reemplaza) una skill escribiendo manifest + script."""
        if is_reserved(manifest.id):
            raise ToolError(f"id reservado por el kernel: '{manifest.id}'")
        if is_tool_id(manifest.id):
            raise ToolError(f"id reservado por el manifiesto de tools: '{manifest.id}'")
        if not isinstance(script, str) or not script.strip():
            raise ToolError(f"script vacío para '{manifest.id}' (una skill necesita código)")
        script_bytes = script.encode("utf-8")
        if len(script_bytes) > self.max_script_bytes:
            raise ToolError(
                f"script de '{manifest.id}' excede el máximo de "
                f"{self.max_script_bytes} bytes"
            )
        skill_dir = self.root / manifest.id
        if skill_dir.exists():
            self._archive_previous(skill_dir)
        skill_dir.mkdir(parents=True, exist_ok=True)
        # ``newline=""``: se escriben los bytes del script tal cual. Sin él, Windows
        # convierte ``\n`` en ``\r\n`` y el hash (que se calcula sobre el archivo)
        # dejaba de ser el histórico ``sha256(script)`` y variaba según el sistema.
        (skill_dir / manifest.entrypoint).write_text(script, encoding="utf-8", newline="")
        # Huella del entrypoint y de los módulos locales que ya existan. El caso
        # de un solo fichero conserva el sha256 histórico del script.
        digest = code_digest(skill_dir, manifest.entrypoint) or sha256_bytes(script_bytes)
        final = SkillManifest.from_dict({**manifest.to_dict(), "hash": digest})
        self._write_manifest(final)
        self._manifests[final.id] = final
        self.index.add_skill(final)
        return final

    def replace_manifest(self, manifest: SkillManifest) -> None:
        """Reescribe el manifest de una skill existente (curator/edición)."""
        if manifest.id not in self._manifests:
            raise ToolError(f"skill no registrada: '{manifest.id}'")
        self._write_manifest(manifest)
        self._manifests[manifest.id] = manifest
        self.index.add_skill(manifest)

    def remove(self, skill_id: str) -> None:
        """Elimina una skill (directorio + índice)."""
        self.get(skill_id)
        shutil.rmtree(self.root / skill_id, ignore_errors=True)
        self._manifests.pop(skill_id, None)
        self.index.remove(skill_id)

    def record_use(self, skill_id: str, success: bool, latency_ms: int = 0) -> SkillManifest:
        """Actualiza estadísticas de uso; cuarentena tras N fallos seguidos."""
        manifest = self.get(skill_id)
        stats = manifest.stats
        updated = SkillStats(
            uses=stats.uses + 1,
            successes=stats.successes + (1 if success else 0),
            failures=stats.failures + (0 if success else 1),
            consecutive_failures=0 if success else stats.consecutive_failures + 1,
            creation_tokens=stats.creation_tokens,
            first_resolution_tokens=stats.first_resolution_tokens,
            total_latency_ms=stats.total_latency_ms + max(0, latency_ms),
        )
        new_manifest = manifest.with_stats(updated)
        if updated.consecutive_failures >= self.max_consecutive_failures:
            new_manifest = new_manifest.with_status(SkillStatus.QUARANTINED)
            self._write_manifest(new_manifest)
            self.quarantine_dir(self.root / skill_id, "fallos consecutivos")
            self._manifests.pop(skill_id, None)
            self.index.remove(skill_id)
            return new_manifest
        self._write_manifest(new_manifest)
        self._manifests[skill_id] = new_manifest
        self.index.add_skill(new_manifest)
        return new_manifest

    def record_creation_tokens(self, skill_id: str, tokens: int) -> SkillManifest:
        """Fija el costo (tokens) de creación de una skill."""
        manifest = self.get(skill_id)
        updated = manifest.stats
        new_stats = SkillStats(
            uses=updated.uses,
            successes=updated.successes,
            failures=updated.failures,
            consecutive_failures=updated.consecutive_failures,
            creation_tokens=tokens,
            first_resolution_tokens=updated.first_resolution_tokens,
            total_latency_ms=updated.total_latency_ms,
        )
        new_manifest = manifest.with_stats(new_stats)
        self._write_manifest(new_manifest)
        self._manifests[skill_id] = new_manifest
        return new_manifest

    def quarantine_dir(self, skill_dir: Path, reason: str) -> None:
        """Mueve un directorio de skill a cuarentena y emite el evento."""
        skill_dir = Path(skill_dir)
        if not skill_dir.exists():
            return
        target = self.root / QUARANTINE_DIR / skill_dir.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(skill_dir), str(target))
        self._emit_quarantined(skill_dir.name, reason)

    # --- helpers internos ---

    def _write_manifest(self, manifest: SkillManifest) -> None:
        skill_dir = self.root / manifest.id
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _archive_previous(self, skill_dir: Path) -> None:
        manifest_path = skill_dir / MANIFEST_NAME
        if not manifest_path.exists():
            return
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        version = str(previous.get("version", "unknown"))
        history_dir = self.root / HISTORY_DIR / skill_dir.name
        history_dir.mkdir(parents=True, exist_ok=True)
        (history_dir / f"{version}.json").write_text(
            json.dumps(previous, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _emit_quarantined(self, skill_id: str, reason: str) -> None:
        if self.sink is None:
            return
        self.sink.emit(
            ExecutionEvent(
                event_type=EventType.SKILL_QUARANTINED,
                task_id=SYSTEM_TASK_ID,
                payload={"skill_id": skill_id, "reason": reason},
            )
        )
