"""Renderizado (Rich) de estados, tablas, eventos y resultados para la TUI."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from satellite.core.events import ExecutionEvent
from satellite.core.task import TaskResult

_EVENT_STYLE: dict[str, str] = {
    "task_started": "bold cyan",
    "graph_built": "cyan",
    "node_started": "yellow",
    "node_finished": "green",
    "tool_called": "magenta",
    "validation_failed": "red",
    "retry": "yellow",
    "node_stuck": "red",
    "task_finished": "bold green",
    "agent_proposed": "bold blue",
    "agent_approved": "green",
    "agent_rejected": "red",
    "agent_created": "bold green",
    "skill_proposed": "blue",
    "skill_approved": "green",
    "skill_executed": "magenta",
    "skill_failed": "red",
    "skill_quarantined": "red",
    "delegation_started": "cyan",
    "delegation_completed": "green",
    "policy_violation": "bold red",
    "safe_mode_entered": "bold yellow",
}

_EVENT_ICON: dict[str, str] = {
    "task_started": "▶",
    "task_finished": "■",
    "graph_built": "◇",
    "node_started": "→",
    "node_finished": "✓",
    "tool_called": "⚙",
    "retry": "↻",
    "node_stuck": "✗",
    "validation_failed": "✗",
    "agent_proposed": "✎",
    "agent_approved": "✓",
    "agent_rejected": "✗",
    "agent_created": "★",
    "delegation_started": "⇢",
    "delegation_completed": "⇠",
    "policy_violation": "⚠",
    "safe_mode_entered": "⛨",
}


def welcome() -> Panel:
    """Panel de bienvenida."""
    body = Text()
    body.append("Satellite 2.0", style="bold cyan")
    body.append("  ·  agente de código con microagentes auto-expandibles\n", style="dim")
    body.append("Escríbele como a un agente: ", style="")
    body.append("«necesito agregar X»", style="italic")
    body.append(", ", style="")
    body.append("«revisa esto»", style="italic")
    body.append(", ", style="")
    body.append("«agrega esto»", style="italic")
    body.append(".\nMantiene la conversación; usa ", style="")
    body.append("/new", style="bold")
    body.append(" para empezar de cero y ", style="")
    body.append("/help", style="bold")
    body.append(" para la paleta.", style="")
    return Panel(body, border_style="cyan", title="satellite", title_align="left")


def reply_panel(reply) -> Panel:
    """Panel con la respuesta del agente."""
    style = {
        "ok": "green",
        "failed": "red",
        "needs_user_input": "yellow",
        "partial": "cyan",
    }.get(getattr(reply, "status", ""), "cyan")
    body = Text()
    body.append("⏺ ", style=f"bold {style}")
    body.append(getattr(reply, "text", "") or "(sin respuesta)")
    error = getattr(reply, "error", None)
    if error and error not in (getattr(reply, "text", "") or ""):
        body.append(f"\n\nerror: {error}", style="red")
    task_id = getattr(reply, "task_id", "")
    status = getattr(reply, "status", "")
    if task_id or status:
        body.append(f"\n\n{task_id} · {status}", style="dim")
    return Panel(body, title="agente", title_align="left", border_style=style)


def plan_options_panel(options) -> Panel:
    """Panel con las alternativas del modo plan."""
    items = getattr(options, "options", ()) or ()
    body = Text()
    question = getattr(options, "question", "")
    if question:
        body.append(question + "\n\n", style="bold")
    recommended = getattr(options, "recommendation", "")
    for index, option in enumerate(items, 1):
        body.append(f"{index}. {option.title}", style="bold cyan")
        if option.id == recommended:
            body.append("  ★ recomendada", style="green")
        body.append("\n")
        body.append(f"   {option.summary}\n")
        for step in option.approach:
            body.append(f"     · {step}\n", style="dim")
        meta = [
            f"esfuerzo {option.effort}" if option.effort else "",
            f"riesgo {option.risk}" if option.risk else "",
        ]
        meta = [item for item in meta if item]
        if meta:
            body.append("   " + " · ".join(meta) + "\n", style="dim")
        if option.tradeoffs:
            body.append(f"   contrapartidas: {option.tradeoffs}\n", style="dim")
        body.append("\n")
    body.append(
        "Elige una opción, o «Otra cosa» para describir tu propio enfoque.",
        style="italic",
    )
    return Panel(
        body, title="modo plan · alternativas", title_align="left", border_style="magenta"
    )


def conversation_table(turns) -> Table:
    """Tabla con la conversación actual."""
    table = Table(title=f"Conversación ({len(turns)} turnos)", title_justify="left", expand=True)
    table.add_column("rol", no_wrap=True)
    table.add_column("mensaje")
    for turn in turns:
        role = getattr(turn, "role", "")
        label = "usuario" if role == "user" else "asistente"
        style = "bold green" if role == "user" else "cyan"
        table.add_row(Text(label, style=style), getattr(turn, "content", ""))
    return table


def status_bar(status) -> Text:
    """Barra de estado de una línea."""
    text = Text()
    text.append(" ", style="")
    text.append(str(status.project_root), style="bold")
    text.append("  ·  ", style="dim")
    text.append(f"agentes {status.agents}", style="cyan")
    text.append("  ·  ", style="dim")
    text.append(f"skills {status.skills}", style="magenta")
    percent = int(getattr(status, "context_percent", 0) or 0)
    if percent:
        from satellite.context.compaction import render_bar, usage_style

        style = {"ok": "dim", "aviso": "yellow", "peligro": "bold red"}[
            usage_style(percent / 100)
        ]
        text.append("  ·  ", style="dim")
        text.append(f"ctx {render_bar(percent / 100, 6, unicode_ok=True)} {percent}%", style=style)
    text.append("  ·  ", style="dim")
    provider = status.provider or "sin proveedor"
    model = f"/{status.model}" if status.model else ""
    text.append(f"{provider}{model}", style="green")
    text.append("  ·  ", style="dim")
    style = {"ask": "yellow", "auto": "green", "unrestricted": "bold red"}.get(
        status.policy_mode, "yellow"
    )
    text.append(f"política {status.policy_mode}", style=style)
    text.append("  ·  ", style="dim")
    text.append(f"sandbox {status.sandbox}", style="blue")
    if status.safe_mode:
        text.append("  ·  ", style="dim")
        text.append("SAFE-MODE", style="bold yellow")
    return text


# Mismo criterio de color que la barra de estado: un solo idioma visual.
_AGENT_STATUS: dict[str, tuple[str, str, str]] = {
    "running": ("◍", "en curso", "cyan"),
    "done": ("●", "hecho", "green"),
    "failed": ("✗", "fallo", "bold red"),
    "idle": ("·", "inactivo", "dim"),
}

CARD_WIDTH = 26


def _clip(text: object, width: int = CARD_WIDTH) -> str:
    """Deja el texto en una línea y dentro del ancho de la tarjeta."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def agent_card(name: str, status: str, action: str = "", detail: str = "") -> Text:
    """Rectángulo de un agente: quién es, cómo va y qué está haciendo."""
    mark, label, style = _AGENT_STATUS.get(str(status), _AGENT_STATUS["idle"])
    text = Text()
    text.append(_clip(name, 14), style="bold cyan")
    text.append("  ")
    text.append(f"{mark} {label}", style=style)
    text.append("\n")
    text.append(
        _clip(action or "sin actividad"),
        style="dim" if status in ("done", "idle") else "bold",
    )
    if detail:
        text.append("\n")
        text.append(_clip(detail), style="dim")
    return text


def agent_table(specs) -> Table:
    """Tabla del catálogo de agentes."""
    table = Table(title=f"Agentes ({len(specs)})", title_justify="left", expand=True)
    table.add_column("id", style="bold cyan", no_wrap=True)
    table.add_column("descripción")
    table.add_column("capabilities", style="dim")
    table.add_column("tools", style="dim")
    table.add_column("can_call", style="dim")
    for spec in specs:
        table.add_row(
            spec.id,
            spec.description,
            ", ".join(spec.capabilities),
            ", ".join(spec.tools) or "—",
            ", ".join(entry.agent_id for entry in spec.can_call) or "—",
        )
    return table


def skill_table(manifests) -> Table:
    """Tabla de skills."""
    table = Table(title=f"Skills ({len(manifests)})", title_justify="left", expand=True)
    table.add_column("id", style="bold magenta", no_wrap=True)
    table.add_column("descripción")
    table.add_column("estado", no_wrap=True)
    table.add_column("usos", justify="right")
    table.add_column("tags", style="dim")
    for manifest in manifests:
        style = "red" if manifest.status.value != "active" else "green"
        table.add_row(
            manifest.id,
            manifest.description,
            Text(manifest.status.value, style=style),
            str(manifest.stats.uses),
            ", ".join(manifest.tags) or "—",
        )
    return table


def stats_table(report) -> Table:
    """Tabla de métricas (línea base marcada como estimación)."""
    table = Table(title="Métricas de skills", title_justify="left", expand=True)
    table.add_column("skill", style="bold magenta", no_wrap=True)
    table.add_column("usos", justify="right")
    table.add_column("ok", justify="right")
    table.add_column("fail", justify="right")
    table.add_column("ahorro", justify="right")
    table.add_column("equilibrio", justify="right")
    for metric in report.skills:
        table.add_row(
            metric.skill_id,
            str(metric.uses),
            str(metric.successes),
            str(metric.failures),
            str(metric.savings),
            "—" if metric.break_even_uses is None else str(metric.break_even_uses),
        )
    if not report.skills:
        table.add_row("(sin skills)", "", "", "", "", "")
    caption = (
        f"ahorro total = {report.total_savings} tokens · "
        "línea base = ESTIMACIÓN · "
        f"búsquedas ok/fallo = {report.search_hits}/{report.search_misses}"
    )
    table.caption = caption
    table.caption_justify = "left"
    return table


def doctor_table(checks) -> Table:
    """Tabla de chequeos de integridad."""
    table = Table(title="Doctor", title_justify="left", expand=True)
    table.add_column("", no_wrap=True)
    table.add_column("chequeo")
    for ok, message in checks:
        table.add_row(Text("✓" if ok else "✗", style="green" if ok else "red"), message)
    return table


def providers_table(session) -> Table:
    """Tabla de proveedores guardados (la api_key nunca se muestra)."""
    table = Table(title="Proveedores", title_justify="left", expand=True)
    table.add_column("", no_wrap=True)
    table.add_column("id", no_wrap=True)
    table.add_column("modelo")
    table.add_column("base_url")
    table.add_column("clave", no_wrap=True)
    saved = session.saved_providers()
    if not saved:
        table.add_row("", "(ninguno)", "", "usa /provider", "")
        return table
    active = session.active_provider_id()
    for provider in saved:
        table.add_row(
            Text("●" if provider.id == active else "", style="green"),
            provider.id,
            provider.model,
            provider.base_url,
            Text(
                "ok" if provider.api_key_set else "sin clave",
                style="green" if provider.api_key_set else "yellow",
            ),
        )
    return table


def search_table(hits) -> Table:
    """Tabla de resultados de búsqueda de skills."""
    table = Table(title=f"Coincidencias ({len(hits)})", title_justify="left", expand=True)
    table.add_column("skill", style="bold magenta", no_wrap=True)
    table.add_column("score", justify="right")
    table.add_column("descripción", style="dim")
    for hit in hits:
        table.add_row(hit.id, f"{hit.score:.3f}", str(hit.fields.get("description", "")))
    return table


def result_panel(result: TaskResult) -> Panel:
    """Panel con el resultado de una tarea."""
    style = {
        "ok": "green",
        "failed": "red",
        "needs_user_input": "yellow",
        "partial": "cyan",
    }.get(result.status.value, "white")
    body = Text()
    body.append(f"estado: {result.status.value}\n", style=f"bold {style}")
    if result.summary:
        body.append(f"{result.summary}\n")
    if result.error:
        body.append(f"\nerror: {result.error}", style="red")
    output = result.output or {}
    if output.get("mode") == "capability_gap":
        gap = output.get("capability_gap") or {}
        body.append(f"\nfalta capacidad: {gap.get('missing', '')}", style="yellow")
    metrics = result.metrics
    body.append(
        f"\n\ntokens {metrics.tokens_in}/{metrics.tokens_out} · "
        f"{metrics.duration_ms} ms · reintentos {metrics.retries}",
        style="dim",
    )
    return Panel(body, title=f"tarea {result.task_id}", title_align="left", border_style=style)


def event_text(event: ExecutionEvent) -> Text:
    """Línea compacta y coloreada para un evento de ejecución."""
    name = event.event_type.value
    style = _EVENT_STYLE.get(name, "white")
    icon = _EVENT_ICON.get(name, "•")
    text = Text()
    text.append(f"{icon} {name}", style=style)
    details: list[str] = []
    if event.node_id:
        details.append(f"nodo={event.node_id}")
    if event.tool_name:
        details.append(f"tool={event.tool_name}")
    payload = event.payload or {}
    for key in ("status", "summary", "agent_id", "proposal_id", "reason", "missing", "error", "attempt"):
        value = payload.get(key)
        if value:
            details.append(f"{key}={value}")
    if details:
        text.append("  " + " · ".join(str(d) for d in details), style="dim")
    return text


def proposal_panel(summary: str, details: dict) -> Panel:
    """Panel con el contenido de una propuesta a aprobar."""
    body = Text()
    body.append(summary + "\n", style="bold")
    for key, value in (details or {}).items():
        body.append(f"\n{key}: ", style="bold dim")
        body.append(str(value))
    return Panel(body, title="aprobación requerida", title_align="left", border_style="yellow")
