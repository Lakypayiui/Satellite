"""Validador determinista de propuestas de agente (AgentProposalValidator).

La seguridad y el rigor NO dependen del LLM: este validador comprueba ids,
allowlists de tools/validators/model_role, placeholders del prompt, dedup y
límites. Si algo falla, se devuelve al creador como error y se reintenta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from satellite.agents.base import AgentRegistry
from satellite.agents.prompts import USER_PLACEHOLDERS, find_placeholders
from satellite.core.proposal import ProposedAgent
from satellite.tool_manifest import TOOL_IDS, is_reserved_id

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_ABSOLUTE_PATH = re.compile(r"([A-Za-z]:[\\/]|/etc/|/home/|/Users/|~/)")

MIN_CAPABILITIES = 2
MAX_CAPABILITIES = 6
MAX_TOOLS = 8
MIN_EXAMPLE_TASKS = 3
_SPLIT_MARKERS = ("y además", " y tambien ", " y también ", "ademas de")

# Regla de conjunto conservadora (F4): control de escritorio no se combina con
# shell ni con capacidades de red en un mismo agente. Es deliberadamente amplia:
# ante la duda, se rechaza la propuesta.
DESKTOP_TOOL_IDS: frozenset[str] = frozenset(TOOL_IDS)
SHELL_TOOL_IDS: frozenset[str] = frozenset(
    {"run_command", "run_argv", "run_python", "job"}
)
_NETWORK_TOKENS: tuple[str, ...] = (
    "network",
    "networking",
    "http",
    "https",
    "web",
    "internet",
    "socket",
    "fetch",
    "download",
    "upload",
    "browser",
)
_SHELL_TOKENS: tuple[str, ...] = (
    "shell",
    "terminal",
    "command",
    "subprocess",
    "exec",
)


@dataclass(frozen=True)
class ValidationReport:
    """Resultado de validar una propuesta."""

    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class AgentProposalValidator:
    """Valida una ``ProposedAgent`` contra el catálogo real y las allowlists."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        tool_ids: set[str] | tuple[str, ...],
        allowed_roles: tuple[str, ...] = ("generated",),
        allowed_validators: tuple[str, ...] = (),
        allowed_placeholders: tuple[str, ...] = (
            "task",
            "context",
            "feedback",
            "output_schema",
            "tools",
        ),
        max_prompt_chars: int = 8000,
        dedup: bool = True,
    ) -> None:
        self.registry = registry
        self.tool_ids = set(tool_ids)
        self.allowed_roles = tuple(allowed_roles)
        self.allowed_validators = set(allowed_validators)
        self.allowed_placeholders = tuple(allowed_placeholders)
        self.max_prompt_chars = max_prompt_chars
        self.dedup = dedup

    def validate(self, proposal: ProposedAgent) -> ValidationReport:
        """Valida la propuesta y devuelve los errores encontrados (si hay)."""
        if proposal.is_clarification:
            return ValidationReport(ok=True)

        errors: list[str] = []
        owner = f"agente '{proposal.id}'"

        if not ID_PATTERN.match(proposal.id):
            errors.append(f"{owner}: id debe cumplir ^[a-z][a-z0-9_]{{2,40}}$")
        if is_reserved_id(proposal.id):
            errors.append(f"{owner}: id reservado (kernel o manifiesto de tools)")

        existing = {spec.id for spec in self.registry.all()}
        if proposal.id in existing:
            errors.append(f"{owner}: ya existe un agente con ese id")

        for tool in proposal.tools:
            if tool not in self.tool_ids:
                errors.append(f"{owner}: tool desconocida '{tool}'")
        if len(proposal.tools) > MAX_TOOLS:
            errors.append(
                f"{owner}: demasiadas tools ({len(proposal.tools)}); máximo {MAX_TOOLS}"
            )

        # Control de escritorio + shell/red en el mismo agente: rechazo conservador.
        tools = set(proposal.tools)
        if tools & DESKTOP_TOOL_IDS:
            capability_text = " ".join(
                [str(item) for item in proposal.capabilities] + [proposal.description]
            ).lower()
            shell_hit = bool(tools & SHELL_TOOL_IDS) or any(
                token in capability_text for token in _SHELL_TOKENS
            )
            net_hit = any(token in capability_text for token in _NETWORK_TOKENS)
            if shell_hit or net_hit:
                motivo = "shell" if shell_hit else ""
                if net_hit:
                    motivo = f"{motivo}+red" if motivo else "red"
                errors.append(
                    f"{owner}: control de escritorio no combinable con {motivo} "
                    "(regla de conjunto conservadora)"
                )

        count = len(proposal.capabilities)
        if count < MIN_CAPABILITIES or count > MAX_CAPABILITIES:
            errors.append(
                f"{owner}: capabilities debe tener entre {MIN_CAPABILITIES} y "
                f"{MAX_CAPABILITIES} (recibidas {count})"
            )

        examples = list(proposal.example_tasks)
        if len(examples) < MIN_EXAMPLE_TASKS:
            errors.append(
                f"{owner}: example_tasks debe incluir al menos {MIN_EXAMPLE_TASKS} "
                f"tareas distintas (recibidas {len(examples)})"
            )
        elif len(set(examples)) != len(examples):
            errors.append(f"{owner}: example_tasks tiene tareas duplicadas")

        description = proposal.description.lower()
        if any(marker in description for marker in _SPLIT_MARKERS):
            errors.append(
                f"{owner}: la descripción tiene dos responsabilidades ('y además'); "
                "divídela en dos agentes"
            )
        for validator_id in proposal.validators:
            if validator_id not in self.allowed_validators:
                errors.append(f"{owner}: validator desconocido '{validator_id}'")

        if proposal.model_role not in self.allowed_roles:
            errors.append(
                f"{owner}: model_role '{proposal.model_role}' no permitido "
                f"(usa {list(self.allowed_roles)})"
            )

        # system_prompt: estático, sin placeholders dinámicos.
        system_placeholders = find_placeholders(proposal.system_prompt)
        dynamic_in_system = sorted(
            {p for p in system_placeholders if p in USER_PLACEHOLDERS}
        )
        if dynamic_in_system:
            errors.append(
                f"{owner}: system_prompt no puede llevar placeholders dinámicos "
                f"{dynamic_in_system} (van en user_template)"
            )
        unknown_system = sorted(
            {p for p in system_placeholders if p not in self.allowed_placeholders}
        )
        if unknown_system:
            errors.append(
                f"{owner}: system_prompt con placeholders no permitidos {unknown_system}"
            )

        # user_template: parte dinámica, con {task} obligatorio.
        user_placeholders = find_placeholders(proposal.user_template)
        unknown_user = sorted(
            {p for p in user_placeholders if p not in self.allowed_placeholders}
        )
        if unknown_user:
            errors.append(
                f"{owner}: user_template con placeholders no permitidos {unknown_user}"
            )
        if "task" not in user_placeholders:
            errors.append(f"{owner}: user_template debe incluir {{task}}")

        total_chars = len(proposal.system_prompt) + len(proposal.user_template)
        if total_chars > self.max_prompt_chars:
            errors.append(
                f"{owner}: system_prompt + user_template exceden "
                f"{self.max_prompt_chars} caracteres"
            )
        for label, text in (
            ("system_prompt", proposal.system_prompt),
            ("user_template", proposal.user_template),
        ):
            if _ABSOLUTE_PATH.search(text):
                errors.append(
                    f"{owner}: {label} contiene rutas absolutas (no reutilizable)"
                )

        for entry in proposal.can_call:
            if entry.agent_id == proposal.id:
                errors.append(f"{owner}: can_call no puede apuntarse a sí mismo")
            elif entry.agent_id not in existing:
                errors.append(
                    f"{owner}: can_call apunta a un agente inexistente '{entry.agent_id}'"
                )

        if self.dedup and not proposal.reuse_checked:
            similar = [
                spec.id for spec in self.registry.find_by_capability(proposal.description)
            ]
            if similar:
                errors.append(
                    f"{owner}: posible duplicado de {similar}; marca reuse_checked "
                    "o cambia el alcance"
                )

        return ValidationReport(ok=not errors, errors=tuple(errors))
