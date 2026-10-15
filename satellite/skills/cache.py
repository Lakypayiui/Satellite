"""Memoización de resultados de skills (sólo si son deterministas).

Reglas ([AJ-4]):

- Sólo se memoizan skills con ``deterministic: true`` en el manifest. Es una
  marca **declarativa**: no se analiza el código del script, sólo se validan los
  ``permissions`` declarados y el lenguaje.
- **Nunca** se memoizan primitivas (el runtime no las ejecuta por aquí).
- La clave incluye el hash del manifest **y** del script, y el hash de contenido
  de los ficheros referenciados por el input (si el input es una ruta).
- **No se cachean fallos**.
- Se invalida al cambiar versión/hash y al entrar en cuarentena.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from satellite.skills.models import ALLOWED_LANGUAGES, SkillManifest, SkillStatus

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


@dataclass(frozen=True)
class CacheStats:
    """Aciertos y fallos de la memoización (para el informe)."""

    hits: int = 0
    misses: int = 0
    entries: int = 0

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
    """Memoiza resultados de skills deterministas (en memoria, con persistencia opcional)."""

    def __init__(self) -> None:
        self._entries: dict[str, object] = {}
        self._keys_by_skill: dict[str, set] = {}
        self.hits = 0
        self.misses = 0

    # --- política ---

    def eligible(self, manifest: SkillManifest, *, allow_network: bool = False) -> bool:
        """¿Se puede memoizar esta skill tal como está declarada?"""
        if not getattr(manifest, "deterministic", False):
            return False
        if manifest.status is not SkillStatus.ACTIVE:
            return False
        if allow_network:
            return False  # si hay que abrir red para ejecutarla, no es determinista
        if manifest.language not in ALLOWED_LANGUAGES:
            return False
        return not unsafe_permissions(manifest.permissions)

    def reason(self, manifest: SkillManifest, *, allow_network: bool = False) -> str:
        """Motivo legible por el que NO se memoiza (``""`` si sí se memoiza)."""
        if not getattr(manifest, "deterministic", False):
            return "la skill no declara deterministic=true"
        if manifest.status is not SkillStatus.ACTIVE:
            return f"estado {manifest.status.value}"
        if allow_network:
            return "la ejecución requiere red"
        if manifest.language not in ALLOWED_LANGUAGES:
            return f"lenguaje no soportado: {manifest.language}"
        unsafe = unsafe_permissions(manifest.permissions)
        if unsafe:
            return f"permisos declarados no deterministas: {', '.join(unsafe)}"
        return ""

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
        """Guarda un resultado **exitoso** (los fallos no se cachean)."""
        if not key:
            return
        self._entries[key] = value
        if skill_id:
            self._keys_by_skill.setdefault(str(skill_id), set()).add(key)

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

    def stats(self) -> CacheStats:
        """Aciertos, fallos y tamaño actual."""
        return CacheStats(hits=self.hits, misses=self.misses, entries=len(self._entries))

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
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "entries": self._entries,
                        "keys_by_skill": {
                            skill: sorted(keys)
                            for skill, keys in self._keys_by_skill.items()
                        },
                        "hits": self.hits,
                        "misses": self.misses,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tmp.replace(target)
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


__all__ = [
    "CacheStats",
    "SkillResultCache",
    "content_digest",
    "unsafe_permissions",
]
