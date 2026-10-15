"""Almacén del catálogo global de agentes (specs + prompts) bajo datos.

Escrituras atómicas, carga tolerante (un spec corrupto se mueve a cuarentena)
y nada fuera del directorio de datos.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from satellite.core.errors import SpecValidationError
from satellite.core.spec import AgentSpec

QUARANTINE_DIR = "_quarantine"


class AgentCatalogStore:
    """Persiste specs (``<id>.json``) y prompts (``<id>.txt``) del catálogo."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.quarantine_dir = self.root / QUARANTINE_DIR

    def save(
        self,
        spec: AgentSpec,
        system: str | None = None,
        user: str | None = None,
    ) -> Path:
        """Guarda el spec y, si se pasan, sus dos mitades de prompt."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{spec.id}.json"
        self._atomic_write(path, json.dumps(spec.to_dict(), indent=2, ensure_ascii=False))
        if system is not None:
            self._atomic_write(self.root / f"{spec.id}.system.txt", system)
        if user is not None:
            self._atomic_write(self.root / f"{spec.id}.user.txt", user)
        return path

    def remove(self, spec_id: str) -> None:
        """Elimina el spec y sus dos ficheros de prompt si existen."""
        for name in (
            f"{spec_id}.json",
            f"{spec_id}.system.txt",
            f"{spec_id}.user.txt",
            f"{spec_id}.txt",
        ):
            path = self.root / name
            if path.exists():
                path.unlink()

    def load_specs(self) -> list[AgentSpec]:
        """Carga todos los specs válidos; los corruptos van a cuarentena."""
        specs: list[AgentSpec] = []
        if not self.root.exists():
            return specs
        for file in sorted(self.root.glob("*.json")):
            try:
                specs.append(AgentSpec.from_dict(json.loads(file.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, SpecValidationError, KeyError, TypeError, ValueError):
                self._quarantine(file, "spec inválido")
        return specs

    def load_prompts(self) -> dict[str, tuple[str, str]]:
        """Pares ``(system, user)`` de los ``<id>.system.txt`` persistidos."""
        prompts: dict[str, tuple[str, str]] = {}
        if not self.root.exists():
            return prompts
        for file in sorted(self.root.glob("*.system.txt")):
            prompt_id = file.name[: -len(".system.txt")]
            user_file = self.root / f"{prompt_id}.user.txt"
            try:
                system = file.read_text(encoding="utf-8")
                user = user_file.read_text(encoding="utf-8") if user_file.exists() else ""
            except OSError:
                continue
            prompts[prompt_id] = (system, user)
        return prompts

    def _atomic_write(self, path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _quarantine(self, file: Path, reason: str) -> None:
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        target = self.quarantine_dir / file.name
        try:
            shutil.move(str(file), str(target))
        except OSError:
            return
