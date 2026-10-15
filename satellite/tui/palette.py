"""Paleta de comandos de la TUI: catálogo y parseo (sin dependencias de UI)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """Un comando de la paleta."""

    name: str
    usage: str
    summary: str


COMMANDS: tuple[Command, ...] = (
    Command("run", "/run <mensaje>", "Envía un mensaje al agente (igual que texto libre)"),
    Command("plan", "/plan <tarea>", "Modo plan: el LLM propone alternativas para elegir"),
    Command("new", "/new", "Empieza una conversación nueva"),
    Command("history", "/history", "Muestra la conversación actual"),
    Command("help", "/help", "Muestra esta ayuda"),
    Command("agents", "/agents", "Lista el catálogo de agentes"),
    Command("create", "/create <necesidad>", "Crea un agente nuevo (con aprobación)"),
    Command("skills", "/skills", "Lista las skills globales"),
    Command("skill-search", "/skill-search <texto>", "Busca skills (BM25, sin LLM)"),
    Command("skill-edit", "/skill-edit <id> [--description <t>] [--tags a,b]", "Edita una skill"),
    Command("skill-delete", "/skill-delete <id>", "Elimina una skill"),
    Command("curate", "/curate [petición]", "Ejecuta el curador de skills"),
    Command("tasks", "/tasks", "Lista las tareas persistidas"),
    Command("status", "/status <task_id>", "Eventos de una tarea"),
    Command("inspect", "/inspect <task_id>", "Grafo serializado de una tarea"),
    Command("stats", "/stats", "Métricas de skills (línea base = estimación)"),
    Command("doctor", "/doctor", "Diagnóstico de integridad"),
    Command("setup", "/setup", "Detecta Docker (sandbox N1)"),
    Command("config", "/config", "Muestra config.json"),
    Command("provider", "/provider [list]", "Configura el proveedor (catálogo + modelos)"),
    Command("mode", "/mode <ask|auto|unrestricted>", "Cambia la política de aprobación"),
    Command("safe-mode", "/safe-mode <on|off>", "Arranque sólo con agentes base"),
    Command("reset", "/reset [--factory]", "Limpia agentes creados (y skills con --factory)"),
    Command("snapshots", "/snapshots", "Lista snapshots"),
    Command("restore", "/restore <nombre>", "Restaura desde un snapshot"),
    Command("clear", "/clear", "Limpia la pantalla"),
    Command("quit", "/quit", "Sale de la TUI"),
)

_BY_NAME = {command.name: command for command in COMMANDS}


def parse(line: str) -> tuple[str, str]:
    """Interpreta una línea: ``/cmd args`` o texto libre (objetivo → ``run``)."""
    text = line.strip()
    if not text:
        return ("", "")
    if text.startswith("/"):
        head, _, rest = text[1:].partition(" ")
        name = head.strip().lower()
        if name == "q":
            name = "quit"
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
    return "\n".join(lines)
