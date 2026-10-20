"""Memoización de resultados de skills (sólo si son deterministas).

Reglas ([AJ-4]):

- Sólo se memoizan skills con ``deterministic: true`` **y** que pasen la
  auditoría estática (:mod:`satellite.skills.audit`): imports no deterministas,
  ejecución dinámica, escrituras o dependencias no auditables descalifican. La
  marca del manifest es declarativa; la auditoría es la que la contrasta.
- ``bash`` **nunca** se memoiza: no se puede auditar estáticamente.
- Sin directorio de skill no se puede auditar ⇒ **no** se memoiza (fail-closed).
- **Nunca** se memoizan primitivas (el runtime no las ejecuta por aquí).
- La clave incluye el hash del manifest **y** del script, y el hash de contenido
  de los ficheros referenciados por el input (si el input es una ruta).
- **No se cachean fallos**, ni resultados cuyo JSON supere ``max_entry_bytes``.
- Se invalida al cambiar versión/hash y al entrar en cuarentena.

Aislamiento (sin prometer de más): el fichero de la cache vive en el directorio
de estado del proyecto, que las tools alcanzan a través de la guardia del kernel
(nunca con una tool). En N1 (Docker) el workspace se monta **solo-lectura**, así
que un contenedor no puede tocarlo; en **N0 (subproceso) no hay aislamiento del
SO** y un script podría escribir ese fichero si se lo propone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from satellite.core.atomic import atomic_write_text
from satellite.skills.audit import Finding, audit_skill, reason_for
from satellite.skills.models import ALLOWED_LANGUAGES, SkillManifest, SkillStatus
from satellite.tool_manifest import is_tool_id

# Permisos declarados que rompen el determinismo (si están activos, no se memoiza).
_EFFECT_KEYS: tuple[str, ...] = (
    "network",
    "write",
    "write_files",
    "filesystem",
    "filesystem_write",
    "clock",
    "time",
    "timestamp",
    "random",
    "uuid",
    "env",
    "environment",
)
_FALSEY = (False, None, 0, "", (), [], {})

MAX_CONTENT_BYTES = 5_000_000
# Tope por entrada: un resultado enorme infla el fichero y no compensa cachearlo.
MAX_ENTRY_BYTES = 256_000
# Tope de entradas: al superarlo se desaloja la más antigua (orden de inserción).
MAX_ENTRIES = 500


@dataclass(frozen=True)
class CacheStats:
    """Aciertos y fallos de la memoización (para el informe)."""

    hits: int = 0
    misses: int = 0
    entries: int = 0
    skipped: int = 0

    @property
    def hit_rate(self) -> float | None:
        """Tasa de aciertos (``None`` si no hubo consultas)."""
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else None

    def to_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "entries": self.entries,
            "skipped": self.skipped,
            "hit_rate": self.hit_rate,
        }


def unsafe_permissions(permissions: object) -> list[str]:
    """Permisos declarados que impiden memoizar (red, escritura, hora, azar, env)."""
    declared = permissions if isinstance(permissions, dict) else {}
    unsafe: list[str] = []
    for key, value in declared.items():
        if str(key).lower() not in _EFFECT_KEYS:
            continue
        if value in _FALSEY:
            continue
        unsafe.append(str(key))
    return sorted(unsafe)


def content_digest(payload: object, project_root: Path | str | None) -> str:
    """sha256 del contenido de los ficheros que referencia el input (ordenado).

    Sólo mira valores de tipo str que apunten a ficheros existentes; si no hay
    ninguno devuelve ``""`` (no hay contenido que pueda cambiar por debajo).
    """
    if project_root is None:
        return ""
    root = Path(project_root)
    candidates: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            candidates.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(payload)
    parts: list[str] = []
    for candidate in sorted(candidates):
        path = Path(candidate)
        if not path.is_absolute():
            path = root / candidate
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_CONTENT_BYTES:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        parts.append(f"{candidate}:{digest}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest() if parts else ""


class SkillResultCache:
    """Memoiza resultados de skills deterministas; persiste si se le da ruta."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        max_entry_bytes: int = MAX_ENTRY_BYTES,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self._entries: dict[str, object] = {}
        self._keys_by_skill: dict[str, set] = {}
        self.hits = 0
        self.misses = 0
        self.skipped = 0
        self._audits: dict[tuple[str, str, str], list[Finding]] = {}
        self.path = Path(path) if path is not None else None
        self.max_entry_bytes = int(max_entry_bytes)
        self.max_entries = int(max_entries)
        if self.path is not None:
            self.load(self.path)

    # --- política ---

    def audit(self, manifest: SkillManifest, skill_dir: Path | str | None) -> list[Finding]:
        """Hallazgos de la auditoría estática (recordada por skill, hash y entrypoint)."""
        if skill_dir is None:
            return [Finding("unreadable", "sin directorio de skill para auditar")]
        root = Path(skill_dir)
        key = (str(root), str(getattr(manifest, "hash", "")), str(manifest.entrypoint))
        findings = self._audits.get(key)
        if findings is None:
            findings = audit_skill(root, manifest.entrypoint, language=manifest.language)
            self._audits[key] = findings
        return findings

    def eligible(
        self,
        manifest: SkillManifest,
        *,
        allow_network: bool = False,
        skill_dir: Path | str | None = None,
    ) -> bool:
        """¿Se puede memoizar? Declaración del manifest **y** auditoría del código."""
        return self.reason(
            manifest, allow_network=allow_network, skill_dir=skill_dir
        ) == ""

    def reason(
        self,
        manifest: SkillManifest,
        *,
        allow_network: bool = False,
        skill_dir: Path | str | None = None,
    ) -> str:
        """Motivo legible por el que NO se memoiza (``""`` si sí se memoiza)."""
        # Ids del manifiesto de tools (fuera del kernel): nunca se memoizan.
        if is_tool_id(str(getattr(manifest, "id", ""))):
            return "id reservado por el manifiesto de tools: no se memoiza"
        if not getattr(manifest, "deterministic", False):
            return "la skill no declara deterministic=true"
        if manifest.status is not SkillStatus.ACTIVE:
            return f"estado {manifest.status.value}"
        if allow_network:
            return "la ejecución requiere red"
        if manifest.language not in ALLOWED_LANGUAGES:
            return f"lenguaje no soportado: {manifest.language}"
        if str(manifest.language).lower() != "python":
            # bash no se puede auditar: sin auditoría no hay determinismo probado.
            return f"lenguaje {manifest.language!r} no auditable"
        unsafe = unsafe_permissions(manifest.permissions)
        if unsafe:
            return f"permisos declarados no deterministas: {', '.join(unsafe)}"
        return reason_for(self.audit(manifest, skill_dir))

    # --- clave ---

    def key_for(
        self,
        manifest: SkillManifest,
        payload: object = None,
        *,
        project_root: Path | str | None = None,
    ) -> str:
        """Clave de memoización: manifest + script + input + contenido referenciado."""
        material = {
            "manifest": json.dumps(
                {
                    "id": manifest.id,
                    "version": manifest.version,
                    "entrypoint": manifest.entrypoint,
                    "language": manifest.language,
                    "permissions": manifest.permissions,
                    "outputs": manifest.outputs,
                    "deterministic": bool(manifest.deterministic),
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "script": manifest.hash or "",
            "input": json.dumps(
                payload if payload is not None else {},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
            "content": content_digest(payload, project_root),
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    # --- almacén ---

    def get(self, key: str) -> object | None:
        """Resultado memoizado (``None`` si no está; cuenta aciertos/fallos)."""
        if key in self._entries:
            self.hits += 1
            return self._entries[key]
        self.misses += 1
        return None

    def put(self, key: str, value: object, *, skill_id: str = "") -> None:
        """Guarda un resultado **exitoso** (los fallos no se cachean).

        Se omiten los resultados cuyo JSON pase de ``max_entry_bytes`` y se
        desaloja la entrada más antigua al superar ``max_entries``.
        """
        if not key:
            return
        if self._too_large(value):
            self.skipped += 1
            return
        self._entries[key] = value
        if skill_id:
            self._keys_by_skill.setdefault(str(skill_id), set()).add(key)
        self._evict_if_needed()
        self.save_if_configured()

    def _too_large(self, value: object) -> bool:
        """True si el resultado no se puede/debe persistir (tope o no serializable)."""
        try:
            size = len(
                json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
            )
        except (TypeError, ValueError):
            return True
        return size > self.max_entry_bytes

    def _evict_if_needed(self) -> None:
        """Desaloja las entradas más antiguas (orden de inserción)."""
        while len(self._entries) > self.max_entries:
            oldest = next(iter(self._entries))
            self._entries.pop(oldest, None)
            for keys in self._keys_by_skill.values():
                keys.discard(oldest)

    def save_if_configured(self) -> None:
        """Persiste si la cache tiene ruta (best-effort y atómico)."""
        if self.path is not None:
            self.save(self.path)

    def invalidate(self, skill_id: str) -> int:
        """Invalida las entradas de esa skill (cuarentena/cambio de versión)."""
        keys = self._keys_by_skill.pop(str(skill_id), set())
        removed = 0
        for key in keys:
            if self._entries.pop(key, None) is not None:
                removed += 1
        return removed

    def clear(self) -> None:
        """Vacía la cache (y los contadores)."""
        self._entries.clear()
        self._keys_by_skill.clear()
        self.hits = 0
        self.misses = 0
        self.skipped = 0

    def stats(self) -> CacheStats:
        """Aciertos, fallos, tamaño actual y entradas omitidas."""
        return CacheStats(
            hits=self.hits,
            misses=self.misses,
            entries=len(self._entries),
            skipped=self.skipped,
        )

    def keys_for(self, skill_id: str) -> set:
        """Claves memoizadas de esa skill (copia)."""
        return set(self._keys_by_skill.get(str(skill_id), set()))

    def __len__(self) -> int:
        return len(self._entries)

    # --- persistencia (opcional) ---

    def save(self, path: Path | str) -> None:
        """Escribe la cache en JSON (atómico); nunca lanza."""
        target = Path(path)
        try:
            atomic_write_text(
                target,
                json.dumps(
                    {
                        "entries": self._entries,
                        "keys_by_skill": {
                            skill: sorted(keys)
                            for skill, keys in self._keys_by_skill.items()
                        },
                        "hits": self.hits,
                        "misses": self.misses,
                        "skipped": self.skipped,
                    },
                    ensure_ascii=False,
                ),
            )
        except (OSError, TypeError):
            return

    def load(self, path: Path | str) -> None:
        """Carga la cache desde JSON (tolerante); ignora lo corrupto."""
        source = Path(path)
        if not source.exists():
            return
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            self._entries.update(entries)
        keys_by_skill = data.get("keys_by_skill")
        if isinstance(keys_by_skill, dict):
            for skill, keys in keys_by_skill.items():
                if isinstance(keys, list):
                    self._keys_by_skill.setdefault(str(skill), set()).update(
                        str(key) for key in keys
                    )
        self.hits = int(data.get("hits", 0) or 0)
        self.misses = int(data.get("misses", 0) or 0)
        self.skipped = int(data.get("skipped", 0) or 0)


__all__ = [
    "CacheStats",
    "SkillResultCache",
    "content_digest",
    "unsafe_permissions",
]
