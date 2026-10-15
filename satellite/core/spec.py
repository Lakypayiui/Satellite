"""AgentSpec: declaración de capacidad de un agente."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from satellite.core.errors import SpecValidationError

# Ciclo de vida de un agente o de una skill. Fuente única: lo usan AgentSpec,
# ProposedAgent y SkillManifest (Anthropic usa el mismo concepto en su API).
ALLOWED_LIFECYCLES: tuple[str, ...] = ("persistent", "ephemeral")


@dataclass(frozen=True)
class CanCallEntry:
    """Agente al que otro puede delegar, con una línea de descripción."""

    agent_id: str
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise SpecValidationError("CanCallEntry: agent_id no puede estar vacío")

    def to_dict(self) -> dict:
        """Serializa la entrada a dict JSON-serializable."""
        return {"agent_id": self.agent_id, "description": self.description}

    @classmethod
    def from_dict(cls, d) -> "CanCallEntry":
        """Construye la entrada desde un dict o un string (sólo el id)."""
        if isinstance(d, str):
            return cls(agent_id=d)
        return cls(agent_id=d["agent_id"], description=d.get("description", ""))


@dataclass(frozen=True)
class AgentSpec:
    """Define qué capacidad tiene un agente (p. ej. ``html_writer``).

    Un agente NO es una tarea única: el trabajo concreto de una ejecución
    vive en ``Node.task``. Esta clase declara la capacidad reutilizable.
    """

    id: str
    name: str
    description: str = ""
    capabilities: tuple[str, ...] = ()
    prompt_ref: str = ""
    tools: tuple[str, ...] = ()
    validators: tuple[str, ...] = ()
    input_schema: dict | None = None
    output_schema: dict | None = None
    model_role: str = "agent"
    max_turns: int = 1
    timeout_s: int = 120
    can_call: tuple[CanCallEntry, ...] = ()
    lifecycle: str = "persistent"

    def __post_init__(self) -> None:
        self._require_non_empty("id", self.id)
        self._require_non_empty("name", self.name)
        self._require_non_empty("prompt_ref", self.prompt_ref)
        for field_name, values in (
            ("capabilities", self.capabilities),
            ("tools", self.tools),
            ("validators", self.validators),
        ):
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise SpecValidationError(
                        f"AgentSpec '{self.id}': {field_name} contiene un "
                        f"valor vacío o no-string: {value!r}"
                    )
        if not self.capabilities:
            raise SpecValidationError(
                f"AgentSpec '{self.id}': capabilities debe tener al menos "
                "una capacidad"
            )
        if not isinstance(self.max_turns, int) or self.max_turns < 0:
            raise SpecValidationError(
                f"AgentSpec '{self.id}': max_turns debe ser int >= 0, "
                f"recibido {self.max_turns!r}"
            )
        if not isinstance(self.timeout_s, int) or self.timeout_s <= 0:
            raise SpecValidationError(
                f"AgentSpec '{self.id}': timeout_s debe ser int > 0, "
                f"recibido {self.timeout_s!r}"
            )
        if not isinstance(self.model_role, str) or not self.model_role.strip():
            raise SpecValidationError(
                f"AgentSpec '{self.id}': model_role no vacío, "
                f"recibido {self.model_role!r}"
            )
        for entry in self.can_call:
            if not isinstance(entry, CanCallEntry):
                raise SpecValidationError(
                    f"AgentSpec '{self.id}': can_call debe contener CanCallEntry"
                )
        if self.lifecycle not in ALLOWED_LIFECYCLES:
            raise SpecValidationError(
                f"AgentSpec '{self.id}': lifecycle debe ser uno de "
                f"{', '.join(ALLOWED_LIFECYCLES)} (recibido {self.lifecycle!r})"
            )

    def _require_non_empty(self, field_name: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise SpecValidationError(
                f"AgentSpec '{self.id}': {field_name} no puede estar vacío"
            )

    def to_dict(self) -> dict:
        """Serializa a dict JSON-serializable (tuplas → listas)."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "prompt_ref": self.prompt_ref,
            "tools": list(self.tools),
            "validators": list(self.validators),
            "input_schema": _deep_copy(self.input_schema),
            "output_schema": _deep_copy(self.output_schema),
            "model_role": self.model_role,
            "max_turns": self.max_turns,
            "timeout_s": self.timeout_s,
            "can_call": [entry.to_dict() for entry in self.can_call],
            "lifecycle": self.lifecycle,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSpec":
        """Construye un AgentSpec desde un dict (acepta listas)."""
        if not isinstance(d, dict):
            raise SpecValidationError(
                f"AgentSpec.from_dict: se esperaba dict, recibido {type(d).__name__}"
            )
        capabilities = _as_tuple(d.get("capabilities", ()), "capabilities")
        tools = _as_tuple(d.get("tools", ()), "tools")
        validators = _as_tuple(d.get("validators", ()), "validators")
        try:
            return cls(
                id=d.get("id", ""),
                name=d.get("name", ""),
                description=d.get("description", ""),
                capabilities=capabilities,
                prompt_ref=d.get("prompt_ref", ""),
                tools=tools,
                validators=validators,
                input_schema=_deep_copy(d.get("input_schema")),
                output_schema=_deep_copy(d.get("output_schema")),
                model_role=d.get("model_role", "agent"),
                max_turns=d.get("max_turns", 1),
                timeout_s=d.get("timeout_s", 120),
                can_call=tuple(
                    CanCallEntry.from_dict(entry) for entry in d.get("can_call", [])
                ),
                lifecycle=d.get("lifecycle", "persistent"),
            )
        except SpecValidationError:
            raise
        except (KeyError, TypeError, AttributeError) as exc:
            raise SpecValidationError(
                f"AgentSpec.from_dict: campos inválidos: {exc}"
            ) from exc
        except ValueError as exc:
            raise SpecValidationError(
                f"AgentSpec.from_dict: valor inválido: {exc}"
            ) from exc


def _as_tuple(values: Any, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise SpecValidationError(
            f"AgentSpec.from_dict: {field_name} debe ser lista o tupla"
        )
    return tuple(str(v) for v in values)


def _deep_copy(value: Any) -> Any:
    """Copia profunda de dicts/listas; devuelve None si es None."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value
