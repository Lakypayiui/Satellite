"""Paleta de comandos de la TUI: catálogo y parseo (sin dependencias de UI).

La lista de comandos **no** se escribe aquí: sale del catálogo compartido
:mod:`satellite.commands`, el mismo que publica la CLI y consume la app. Esta capa sólo
le da forma (nombre/uso/resumen) y añade el parseo de la línea.
"""

from __future__ import annotations

from dataclasses import dataclass

from satellite.commands import for_surface


@dataclass(frozen=True)
class Command:
    """Un comando de la paleta, visto para la TUI."""

    name: str
    usage: str
    summary: str


# Una sola definición para las tres interfaces: ver :mod:`satellite.commands`.
COMMANDS: tuple[Command, ...] = tuple(
    Command(spec.id, spec.usage, spec.summary) for spec in for_surface("tui")
)

_BY_NAME = {command.name: command for command in COMMANDS}

# Alias de tecleo: no crean comandos nuevos, sólo apuntan a uno existente.
_ALIASES = {"q": "quit", "model": "provider", "models": "provider"}


def parse(line: str) -> tuple[str, str]:
    """Interpreta una línea: ``/cmd args`` o texto libre (objetivo → ``run``)."""
    text = line.strip()
    if not text:
        return ("", "")
    if text.startswith("/"):
        head, _, rest = text[1:].partition(" ")
        name = head.strip().lower()
        name = _ALIASES.get(name, name)
        return (name, rest.strip())
    return ("run", text)


def get(name: str) -> Command | None:
    """Comando por nombre (None si no existe)."""
    return _BY_NAME.get(name)


def command_names() -> list[str]:
    """Nombres de todos los comandos."""
    return [command.name for command in COMMANDS]


def help_text() -> str:
    """Ayuda formateada (texto plano)."""
    width = max(len(command.usage) for command in COMMANDS)
    lines = ["Comandos disponibles:", ""]
    for command in COMMANDS:
        lines.append(f"  {command.usage.ljust(width)}  {command.summary}")
    lines.append("")
    lines.append("  (texto libre)  Envía un mensaje al agente (conversación)")
    lines.append("  @contexto      Adjunta contexto: @src/api.py, @src/, @login (símbolo)")
    lines.append("  /model         Abre el selector rápido de proveedor/modelo (alias de /provider)")
    return "\n".join(lines)
