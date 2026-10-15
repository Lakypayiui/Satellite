"""PromptRegistry por defecto con plantillas en PAR (system_prompt + user_template).

- ``system_prompt`` es **estático**: NO puede llevar placeholders dinámicos
  (``{task}``, ``{context}``, ``{feedback}``). Sí puede llevar ``{tools}`` y
  ``{output_schema}``, que se resuelven de forma determinista en build time.
- ``user_template`` lleva la parte dinámica: ``{task}``, ``{context}``,
  ``{feedback}``.

Este reparto es lo que permite cachear el prefijo: el system no cambia entre
llamadas del mismo agente.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

from satellite.agents.base import PromptRegistry

SYSTEM_SUFFIX = ".system.txt"
USER_SUFFIX = ".user.txt"


class _SafeDict(dict):
    """dict con ``__missing__`` → "" para render seguro sin excepción."""

    def __missing__(self, key):
        return ""


class DefaultPromptRegistry(PromptRegistry):
    """Registro de prompts en pares (system, user); ``get`` devuelve el system."""

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self._prompts: dict[str, tuple[str, str]] = {}
        self.legacy: set[str] = set()
        for prompt_id, template in (templates or {}).items():
            self.register(prompt_id, template)

    def register(self, prompt_id: str, template: str) -> None:
        """Compatibilidad: registra un prompt único como ``system`` (user vacío)."""
        self._prompts[prompt_id] = (template, "")

    def register_pair(self, prompt_id: str, system: str, user: str = "") -> None:
        """Registra el par system/user de un agente."""
        self._prompts[prompt_id] = (system, user)
        self.legacy.discard(prompt_id)

    def get(self, prompt_id: str) -> str:
        """Devuelve el ``system_prompt`` (compatibilidad)."""
        return self.get_system(prompt_id)

    def get_system(self, prompt_id: str) -> str:
        """Devuelve el system prompt; KeyError claro si no existe."""
        return self._pair(prompt_id)[0]

    def get_user(self, prompt_id: str) -> str:
        """Devuelve el user template; KeyError claro si no existe."""
        return self._pair(prompt_id)[1]

    def ids(self) -> list[str]:
        """Ids registrados, ordenados."""
        return sorted(self._prompts)

    def load_dir(self, dir_path: Path) -> list[str]:
        """Carga pares ``<id>.system.txt`` (+ ``<id>.user.txt``).

        Por compatibilidad también acepta ficheros sueltos ``<id>.txt``, que se
        registran como system y se marcan en ``self.legacy`` (la migración los
        convierte al par).
        """
        directory = Path(dir_path)
        loaded: list[str] = []
        for file in sorted(directory.glob("*" + SYSTEM_SUFFIX)):
            prompt_id = file.name[: -len(SYSTEM_SUFFIX)]
            user_file = directory / f"{prompt_id}{USER_SUFFIX}"
            user = user_file.read_text(encoding="utf-8") if user_file.exists() else ""
            self.register_pair(prompt_id, file.read_text(encoding="utf-8"), user)
            loaded.append(prompt_id)

        known = set(loaded)
        for file in sorted(directory.glob("*.txt")):
            if file.name.endswith(SYSTEM_SUFFIX) or file.name.endswith(USER_SUFFIX):
                continue
            if file.stem in known:
                continue
            self.register(file.stem, file.read_text(encoding="utf-8"))
            self.legacy.add(file.stem)
            loaded.append(file.stem)
        return loaded

    def render(self, prompt_id: str, **kwargs) -> str:
        """Renderiza el system con ``format_map`` seguro (faltantes → '')."""
        template = self.get_system(prompt_id)
        return string.Formatter().vformat(template, (), _SafeDict(kwargs))

    def _pair(self, prompt_id: str) -> tuple[str, str]:
        try:
            return self._prompts[prompt_id]
        except KeyError:
            raise KeyError(f"prompt no encontrado: '{prompt_id}'") from None


ALLOWED_PLACEHOLDERS: tuple[str, ...] = (
    "task",
    "context",
    "feedback",
    "output_schema",
    "tools",
)

# Resueltos en build time dentro del system (deterministas, no dinámicos).
SYSTEM_PLACEHOLDERS: tuple[str, ...] = ("tools", "output_schema")
# Dinámicos: viven en el user template.
USER_PLACEHOLDERS: tuple[str, ...] = ("task", "context", "feedback")

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def find_placeholders(template: str) -> list[str]:
    """Devuelve los placeholders ``{nombre}`` presentes en la plantilla."""
    return _PLACEHOLDER_RE.findall(template)


def render_template(template: str, values: dict | None = None, **kwargs) -> str:
    """Sustituye SOLO los placeholders permitidos y deja el resto intacto.

    A diferencia de ``str.format_map``, los literales con llaves (p. ej. los
    ejemplos JSON dentro de un prompt) no rompen la renderización.
    """
    merged: dict = dict(values or {})
    merged.update(kwargs)
    rendered = template
    for name in ALLOWED_PLACEHOLDERS:
        rendered = rendered.replace("{" + name + "}", str(merged.get(name, "")))
    return rendered


def render_system(system_prompt: str, *, tools: str = "", output_schema: str = "") -> str:
    """Sustituye SOLO ``{tools}`` y ``{output_schema}`` en el system.

    Deja intactas las menciones a ``{task}``/``{context}``/``{feedback}``: en el
    system son documentación, no valores a sustituir. Así el system es estable
    entre llamadas (cacheable) y nunca recibe contenido dinámico.
    """
    rendered = system_prompt
    rendered = rendered.replace("{tools}", str(tools))
    rendered = rendered.replace("{output_schema}", str(output_schema))
    return rendered
