"""AgentRegistry por defecto con carga desde disco y búsqueda por capacidad."""

from __future__ import annotations

import json
from pathlib import Path

from satellite.agents.base import AgentRegistry
from satellite.core.errors import SpecValidationError
from satellite.core.spec import AgentSpec


class DefaultAgentRegistry(AgentRegistry):
    """Catálogo de specs; carga dirs y permite override por id."""

    def __init__(self, specs: dict[str, AgentSpec] | None = None) -> None:
        self._specs: dict[str, AgentSpec] = dict(specs or {})
        self.errors: dict[str, str] = {}

    def register_spec(self, spec: AgentSpec) -> None:
        """Registra; SpecValidationError si el id ya existe."""
        if spec.id in self._specs:
            raise SpecValidationError(f"spec duplicado: '{spec.id}'")
        self._specs[spec.id] = spec

    def get(self, spec_id: str) -> AgentSpec:
        """Devuelve el spec; KeyError claro si no existe."""
        try:
            return self._specs[spec_id]
        except KeyError:
            raise KeyError(f"spec no encontrado: '{spec_id}'") from None

    def find_by_capability(self, text: str) -> list[AgentSpec]:
        """Busca por palabras clave case-insensitive en description+capabilities.

        Cada palabra del query debe aparecer (substring) en el texto del
        spec; devuelve ordenados por nº de coincidencias desc.
        """
        tokens = [t for t in text.lower().split() if t]
        scored: list[tuple[int, AgentSpec]] = []
        for spec in self.all():
            haystack = f"{spec.description.lower()} {' '.join(spec.capabilities).lower()}"
            matched = sum(1 for t in tokens if t in haystack)
            if matched > 0:
                scored.append((matched, spec))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [spec for _, spec in scored]

    def all(self) -> list[AgentSpec]:
        """Todos los specs registrados."""
        return list(self._specs.values())

    def replace_spec(self, spec: AgentSpec) -> None:
        """Registra o SOBRESCRIBE un spec (extensión de un agente existente)."""
        self._specs[spec.id] = spec

    def load_dir(self, dir_path: Path, *, tolerant: bool = True) -> list[str]:
        """Carga ``*.json`` como ``AgentSpec`` (sobreescribe por id).

        Con ``tolerant=True`` (por defecto) un archivo inválido se salta y se
        anota en ``self.errors``; nunca impide el arranque.
        """
        loaded: list[str] = []
        for file in sorted(Path(dir_path).glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                spec = AgentSpec.from_dict(data)
            except (OSError, json.JSONDecodeError, SpecValidationError, KeyError, TypeError, ValueError) as exc:
                if not tolerant:
                    raise
                self.errors[file.name] = str(exc)
                continue
            self._specs[spec.id] = spec
            loaded.append(spec.id)
        return loaded
