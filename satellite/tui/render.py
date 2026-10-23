"""Renderizado (Rich) de estados, tablas, eventos y resultados para la TUI."""

from __future__ import annotations

import difflib
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from satellite.core.events import ExecutionEvent
from satellite.core.task import TaskResult

# Paleta Obsidian/Dark Slate compartida por tarjetas y barra superior.
ACCENT = "#38bdf8"
SUCCESS = "#22c55e"
DANGER = "#ef4444"
MUTED = "#94a3b8"
CARD_BG = "#1e293b"

ASSISTANT_BADGE = "🤖 Satellite"
USER_BADGE = "👤 Tú"
PREVIEW_LINES = 10

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
    "tool_called": "›",
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


def _fmt_ms(ms: float) -> str:
    """Duración legible (ms o segundos con un decimal)."""
    value = float(ms or 0)
    if value <= 0:
        return ""
    return f"{value:.0f} ms" if value < 1000 else f"{value / 1000:.1f} s"


def _reply_footer(reply, *, elapsed_ms: float = 0, metrics: dict | None = None) -> Text:
    """Pie de la respuesta: tiempo, tokens y estado."""
    metrics = metrics or getattr(reply, "metrics", None) or {}
    footer = Text()
    resumen: list[str] = []
    if elapsed_ms:
        resumen.append(_fmt_ms(elapsed_ms))
    tokens_in = int(metrics.get("tokens_in", 0) or 0)
    tokens_out = int(metrics.get("tokens_out", 0) or 0)
    if tokens_in or tokens_out:
        resumen.append(f"tokens {tokens_in}/{tokens_out}")
    trozos: list[tuple[str, str]] = []
    if resumen:
        trozos.append((" · ".join(resumen), "dim"))
    error = getattr(reply, "error", None)
    if error and error not in (getattr(reply, "text", "") or ""):
        trozos.append((f"error: {error}", DANGER))
    meta = [value for value in (getattr(reply, "task_id", ""), getattr(reply, "status", "")) if value]
    if meta:
        trozos.append((" · ".join(meta), "dim"))
    for index, (chunk, style) in enumerate(trozos):
        if index:
            footer.append("   ")
        footer.append(chunk, style=style)
    return footer


def user_card(text: str) -> Panel:
    """Tarjeta compacta del mensaje del usuario."""
    contenido = str(text or "").strip() or "(mensaje vacío)"
    return Panel(
        Text(contenido),
        title=USER_BADGE,
        title_align="left",
        border_style=SUCCESS,
        padding=(0, 1),
    )


def reply_panel(reply, *, elapsed_ms: float = 0, metrics: dict | None = None) -> Panel:
    """Panel con la respuesta del agente (markdown, código resaltado y pie con métricas)."""
    status = getattr(reply, "status", "")
    accent = {
        "ok": SUCCESS,
        "failed": DANGER,
        "needs_user_input": "yellow",
        "partial": ACCENT,
    }.get(status, ACCENT)
    text = getattr(reply, "text", "") or "(sin respuesta)"
    body = Group(
        Markdown(text),
        Text(""),
        _reply_footer(reply, elapsed_ms=elapsed_ms, metrics=metrics),
    )
    return Panel(
        body,
        title=ASSISTANT_BADGE,
        title_align="left",
        border_style=accent,
        padding=(0, 1),
    )


def conversation_table(turns) -> Table:
    """Tabla con la conversación actual."""
    table = Table(title=f"Conversación ({len(turns)} turnos)", title_justify="left", expand=True)
    table.add_column("rol", no_wrap=True)
    table.add_column("mensaje")
    for turn in turns:
        role = getattr(turn, "role", "")
        label = USER_BADGE if role == "user" else ASSISTANT_BADGE
        style = "bold green" if role == "user" else "cyan"
        table.add_row(Text(label, style=style), getattr(turn, "content", ""))
    return table


def _short_path(path: object, *, parts: int = 2) -> str:
    """Últimos tramos de una ruta (para la píldora del workspace)."""
    if not path:
        return ""
    chunks = [c for c in str(path).replace("\\", "/").rstrip("/").split("/") if c]
    return "/".join(chunks[-parts:]) if chunks else str(path)


def git_branch(project_root) -> str:
    """Rama git del proyecto (o el sha corto); ``""`` si no aplica."""
    try:
        root = Path(project_root)
    except TypeError:
        return ""
    head = root / ".git" / "HEAD"
    try:
        if not head.exists():
            gitfile = root / ".git"
            if gitfile.is_file():
                content = gitfile.read_text(encoding="utf-8", errors="replace").strip()
                if content.startswith("gitdir:"):
                    target = Path(content.split(":", 1)[1].strip())
                    if not target.is_absolute():
                        target = (root / target).resolve()
                    head = target / "HEAD"
            if not head.exists():
                return ""
        raw = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not raw:
        return ""
    if raw.startswith("ref:"):
        ref = raw.split(":", 1)[1].strip()
        prefijo = "refs/heads/"
        # Las ramas pueden llevar "/" (feature/x): sólo se retira el prefijo de refs.
        return ref[len(prefijo):] if ref.startswith(prefijo) else ref.rsplit("/", 1)[-1]
    return raw[:7]


def _pill(text: Text, label: str, style: str) -> None:
    """Añade una píldora (etiqueta con fondo) a la barra superior."""
    text.append(" ")
    text.append(f" {label} ", style=style)


def status_bar(status) -> Text:
    """Barra superior con píldoras: workspace, modelo, rama, sandbox y política."""
    text = Text()
    text.append(" ")
    workspace = _short_path(getattr(status, "project_root", ""))
    if workspace:
        _pill(text, f"📁 {workspace}", f"#e2e8f0 on {CARD_BG}")
    provider = getattr(status, "provider", "") or ""
    model = getattr(status, "model", "") or ""
    if provider or model:
        etiqueta = f"🤖 {provider}" + (f"/{model}" if model else "")
        _pill(text, etiqueta, f"bold #7dd3fc on {CARD_BG}")
    branch = git_branch(getattr(status, "project_root", ""))
    if branch:
        _pill(text, f"⎇ {branch}", f"#a7f3d0 on {CARD_BG}")
    sandbox = getattr(status, "sandbox", "")
    if sandbox:
        _pill(text, f"🧱 sandbox {sandbox}", f"#bae6fd on {CARD_BG}")
    policy = getattr(status, "policy_mode", "") or ""
    if policy:
        policy_style = {"ask": "yellow", "auto": "green", "unrestricted": "bold red"}.get(
            policy, "yellow"
        )
        _pill(text, f"🛡 {policy}", f"{policy_style} on {CARD_BG}")
    if getattr(status, "safe_mode", False):
        _pill(text, "SAFE-MODE", "bold #0f172a on #facc15")

    detalle = Text()
    detalle.append("  ")
    detalle.append(f"agentes {getattr(status, 'agents', 0)}", style="dim")
    detalle.append("  ·  ", style="dim")
    detalle.append(f"skills {getattr(status, 'skills', 0)}", style="dim")
    percent = int(getattr(status, "context_percent", 0) or 0)
    if percent:
        from satellite.context.compaction import render_bar, usage_style

        style = {"ok": "dim", "aviso": "yellow", "peligro": "bold red"}[
            usage_style(percent / 100)
        ]
        detalle.append("  ·  ", style="dim")
        detalle.append(f"ctx {render_bar(percent / 100, 6, unicode_ok=True)} {percent}%", style=style)
    text.append_text(detalle)
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
        for opcion in output.get("options") or []:
            body.append(f"\n  · {opcion}", style="yellow")
    metrics = result.metrics
    body.append(
        f"\n\ntokens {metrics.tokens_in}/{metrics.tokens_out} · "
        f"{metrics.duration_ms} ms · reintentos {metrics.retries}",
        style="dim",
    )
    return Panel(body, title=f"tarea {result.task_id}", title_align="left", border_style=style)


_READ_TOOLS = ("read_file",)
_WRITE_TOOLS = ("write_file", "edit_file")
_TERMINAL_TOOLS = ("run_command", "run_shell", "run_argv", "run_python")
_LISTING_TOOLS = ("search_files", "list_dir")


def _basename(value: object, fallback: str) -> str:
    """Último tramo de una ruta (o el valor entero si no es una ruta)."""
    if not value:
        return fallback
    chunks = [c for c in str(value).replace("\\", "/").rstrip("/").split("/") if c]
    return chunks[-1] if chunks else fallback


def tool_action(tool: str, arguments: dict | None = None) -> str:
    """Acción en presente y semántica para la tarjeta del agente (sin engranes)."""
    args = arguments or {}
    name = str(tool or "")
    if name == "read_file":
        return f"Leyendo {_basename(args.get('path'), 'un archivo')}"
    if name == "write_file":
        return f"Escribiendo {_basename(args.get('path'), 'un archivo')}"
    if name == "edit_file":
        return f"Editando {_basename(args.get('path'), 'un archivo')}"
    if name in ("run_command", "run_shell", "run_argv"):
        command = str(args.get("command") or "").strip()
        cabeza = command.split()[0] if command else ""
        return f"Ejecutando {cabeza}" if cabeza else "Ejecutando un comando"
    if name == "run_python":
        return "Ejecutando Python"
    if name == "search_files":
        return f"Buscando {args.get('pattern') or 'archivos'}"
    if name == "list_dir":
        return f"Listando {_basename(args.get('path'), 'carpeta')}"
    return f"Usando {name}" if name else "trabajando"


def _visible_lines(lines: list[str], limit: int = PREVIEW_LINES) -> tuple[list[str], int]:
    """Recorta una lista de líneas y dice cuántas quedaron fuera."""
    if len(lines) <= limit:
        return lines, 0
    return lines[:limit], len(lines) - limit


def _diff_body(old: str, new: str) -> Text:
    """Diff unificado de un fragmento, con color real (+ verde, - rojo)."""
    body = Text()
    lines = list(
        difflib.unified_diff(
            (old or "").splitlines(), (new or "").splitlines(), "antes", "después", lineterm=""
        )
    )
    if not lines:
        body.append("(sin cambios)", style="dim")
        return body
    visibles, restantes = _visible_lines(lines)
    for line in visibles:
        if line.startswith(("+++", "---")):
            body.append(line + "\n", style="bold")
        elif line.startswith("@@"):
            body.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            body.append(line + "\n", style=SUCCESS)
        elif line.startswith("-"):
            body.append(line + "\n", style=DANGER)
        else:
            body.append(line + "\n", style="dim")
    if restantes:
        body.append(f"… {restantes} líneas más\n", style="dim")
    return body


def _added_body(content: str) -> Text:
    """Contenido nuevo de un ``write_file`` como líneas añadidas (verde)."""
    body = Text()
    lines = (content or "").splitlines() or [""]
    visibles, restantes = _visible_lines(lines)
    for line in visibles:
        body.append(f"+ {line}\n", style=SUCCESS)
    if restantes:
        body.append(f"… {restantes} líneas más\n", style="dim")
    return body


def _terminal_body(output: object, *, success: bool, error: str) -> Text:
    """Caja de terminal: salida truncada con stderr en rojo."""
    body = Text()
    if isinstance(output, dict):
        exit_code = output.get("exit_code")
        if exit_code is not None:
            body.append(f"exit {exit_code}\n", style=SUCCESS if success else DANGER)
        for stream, style in (("stdout", ""), ("stderr", DANGER)):
            text = output.get(stream)
            if not isinstance(text, str) or not text.strip():
                continue
            visibles, restantes = _visible_lines(text.rstrip("\n").splitlines())
            body.append(f"$ {stream}\n", style="bold dim")
            for line in visibles:
                body.append(f"  {line}\n", style=style)
            if restantes:
                body.append(f"  … {restantes} líneas más\n", style="dim")
    elif isinstance(output, str) and output.strip():
        visibles, restantes = _visible_lines(output.rstrip("\n").splitlines())
        for line in visibles:
            body.append(f"  {line}\n")
        if restantes:
            body.append(f"  … {restantes} líneas más\n", style="dim")
    if not success and error:
        body.append(error + "\n", style=DANGER)
    if not body.plain:
        body.append("(sin salida)", style="dim")
    return body


def _listing_body(output: object, *, error: str) -> Text:
    """Resultado de una búsqueda/listado: cuántos y una muestra."""
    items = output if isinstance(output, list) else []
    body = Text()
    body.append(f"{len(items)} resultado(s)\n", style="bold")
    for item in items[:6]:
        if isinstance(item, dict):
            label = item.get("path") or item.get("name") or ""
        else:
            label = str(item)
        body.append(f"  · {_clip(label, 80)}\n", style=ACCENT)
    if len(items) > 6:
        body.append(f"  … {len(items) - 6} más\n", style="dim")
    if error:
        body.append(error + "\n", style=DANGER)
    return body


def action_card(
    tool: str,
    arguments: dict | None = None,
    *,
    success: bool = True,
    error: str = "",
    output: object = None,
) -> Panel:
    """Tarjeta semántica de una llamada a herramienta (Read/Update/Terminal/Search)."""
    args = arguments or {}
    name = str(tool or "")
    ok_border = DANGER if not success else ACCENT
    padding = (0, 1)

    if name in _READ_TOOLS:
        path = str(args.get("path") or "")
        body = Text()
        content = output if isinstance(output, str) else ""
        if content:
            total = len(content.splitlines())
            body.append(f"{total} líneas", style="bold")
            muestra, _ = _visible_lines([line for line in content.splitlines()], 4)
            resumen = " ".join(line.strip() for line in muestra if line.strip())
            if resumen:
                body.append("\n" + _clip(resumen, 120), style="dim")
        else:
            body.append("contenido leído", style="dim")
        if not success and error:
            body.append("\n" + error, style=DANGER)
        return Panel(
            body, title=f"📖 Read {path or 'archivo'}", title_align="left",
            border_style=ok_border, padding=padding,
        )

    if name in _WRITE_TOOLS:
        path = str(args.get("path") or "")
        if name == "edit_file":
            body = _diff_body(str(args.get("old_string") or ""), str(args.get("new_string") or ""))
        else:
            body = _added_body(str(args.get("content") or ""))
        if not success and error:
            body.append(error + "\n", style=DANGER)
        return Panel(
            body, title=f"📝 Update {path or 'archivo'}", title_align="left",
            border_style=DANGER if not success else SUCCESS, padding=padding,
        )

    if name in _TERMINAL_TOOLS:
        command = str(args.get("command") or args.get("code") or "").strip()
        return Panel(
            _terminal_body(output, success=success, error=error),
            title=f"❯ Terminal {_clip(command, 60) or 'comando'}",
            title_align="left", border_style=ok_border, padding=padding,
        )

    if name in _LISTING_TOOLS:
        target = str(args.get("pattern") or args.get("path") or "")
        return Panel(
            _listing_body(output, error=error),
            title=f"🔍 Search {_clip(target, 60) or 'proyecto'}",
            title_align="left", border_style=ok_border, padding=padding,
        )

    body = Text(_clip(args, 120), style="dim") if args else Text("ejecutado", style="dim")
    if not success and error:
        body.append("\n" + error, style=DANGER)
    return Panel(
        body, title=f"🔧 {name or 'tool'}", title_align="left",
        border_style=ok_border, padding=padding,
    )


# Eventos de ciclo de vida: alimentan el panel de agentes y la línea de estado, pero no
# son conversación y no se pintan en el log.
LIFECYCLE_EVENTS = frozenset(
    {"task_started", "task_finished", "graph_built", "node_started", "node_finished"}
)
# Tools que sólo miran el proyecto: una línea basta. Escrituras y comandos llevan
# su tarjeta con el diff o la salida.
_QUIET_TOOLS = _READ_TOOLS + _LISTING_TOOLS + ("lsp", "git_status", "request_context")


def tool_line(tool: str, arguments: dict | None = None, *, success: bool = True, error: str = "") -> Text:
    """Una línea compacta para una tool de solo lectura."""
    line = Text("  · ", style="dim")
    line.append(tool_action(tool, arguments), style="dim" if success else DANGER)
    if not success and error:
        line.append(f"  ({_clip(error, 80)})", style=DANGER)
    return line


def delegation_line(event: ExecutionEvent) -> Text:
    """Inicio o fin de una delegación en una línea («→ python_coder»)."""
    payload = event.payload or {}
    agent = str(payload.get("agent_id") or "agente")
    if event.event_type.value == "delegation_started":
        return Text.assemble(("  → ", ACCENT), (agent, "bold"), (" trabajando", "dim"))
    ok = str(payload.get("status") or "ok") == "ok"
    return Text.assemble(
        ("  ✓ " if ok else "  ✗ ", SUCCESS if ok else DANGER),
        (agent, "bold"),
        (" terminó" if ok else f" terminó con estado {payload.get('status')}", "dim"),
    )


def reconnect_line(event: ExecutionEvent) -> Text:
    """Reintento del proveedor («↻ reconectando con el proveedor 2/4 en 2 s»)."""
    payload = event.payload or {}
    line = Text("  ↻ ", style="yellow")
    line.append(
        f"reconectando con el proveedor {payload.get('attempt', '?')}/{payload.get('max', '?')}"
        f" en {payload.get('wait_s', '?')} s",
        style="yellow",
    )
    if payload.get("error"):
        line.append(f"  ({_clip(str(payload['error']), 80)})", style="dim")
    return line


def is_reconnect(event: ExecutionEvent) -> bool:
    return event.event_type.value == "retry" and (event.payload or {}).get("kind") == "provider"


def live_panel(text: str) -> Panel:
    """La respuesta mientras el modelo la escribe."""
    return Panel(
        Markdown(text or "…"),
        title=ASSISTANT_BADGE,
        title_align="left",
        border_style=MUTED,
        padding=(0, 1),
    )


def question_hint(question: dict | None) -> Text | None:
    """Cómo responder a una pregunta con opciones (``None`` si no hay opciones)."""
    choices = (question or {}).get("choices") or []
    if not choices:
        return None
    return Text(
        f"responde con el número (1-{len(choices)}) o escribe tu propia respuesta",
        style="italic dim",
    )


def is_quiet_tool(tool: str) -> bool:
    return tool in _QUIET_TOOLS


def event_text(event: ExecutionEvent):
    """Render de un evento: tarjeta semántica para tool_called, línea compacta si no."""
    payload = event.payload or {}
    if event.event_type.value == "tool_called":
        tool = str(getattr(event, "tool_name", "") or payload.get("tool", ""))
        return action_card(
            tool,
            payload.get("arguments"),
            success=bool(payload.get("success", True)),
            error=str(payload.get("error") or ""),
            output=payload.get("output"),
        )
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
    for key in ("status", "summary", "agent_id", "proposal_id", "reason", "missing", "error", "attempt"):
        value = payload.get(key)
        if value:
            details.append(f"{key}={value}")
    if details:
        text.append("  " + " · ".join(str(d) for d in details), style="dim")
    return text


_APPROVAL_TITLES = {
    "path": "acceso fuera del proyecto",
    "context_path": "leer fuera del proyecto (una vez)",
    "delegation": "permiso de delegación",
    "permission_escalation": "escalado de permisos",
}


def proposal_panel(summary: str, details: dict, *, kind: str = "agent") -> Panel:
    """Panel con el contenido de una propuesta a aprobar."""
    body = Text()
    body.append(summary + "\n", style="bold")
    for key, value in (details or {}).items():
        body.append(f"\n{key}: ", style="bold dim")
        body.append(str(value), style="bold" if key == "path" else "")
    return Panel(
        body,
        title=_APPROVAL_TITLES.get(str(kind), "aprobación requerida"),
        title_align="left",
        border_style="yellow",
    )


CHANGES_DIFF_LINES = 400
CHANGES_TAIL_LINES = 60


def changes_panel(
    task_id: str,
    files: list[str],
    diff_text: str,
    *,
    limit: int = CHANGES_DIFF_LINES,
    hint: str = "",
) -> Panel:
    """Panel con los archivos que tocó una tarea y su diff (recortado si es enorme)."""
    body = Text()
    if files:
        body.append(f"{len(files)} archivo(s) modificado(s)\n", style="bold")
        for name in files:
            body.append(f"  · {name}\n", style="cyan")
    else:
        body.append("(sin cambios registrados)\n", style="dim")

    lines = diff_text.splitlines()
    if lines:
        visibles = lines[:limit]
        body.append("\n")
        for line in visibles:
            style = ""
            if line.startswith(("+++", "---")):
                style = "bold"
            elif line.startswith("+"):
                style = "green"
            elif line.startswith("-"):
                style = "red"
            elif line.startswith("@@"):
                style = "cyan"
            elif line.startswith("\\"):
                style = "dim"
            body.append(f"{line}\n", style=style)
        restantes = len(lines) - len(visibles)
        if restantes > 0:
            aviso = f"… {restantes} líneas más"
            body.append(f"\n{aviso} ({hint})\n" if hint else f"\n{aviso}\n", style="dim")
    return Panel(
        body,
        title=f"cambios de {task_id or 'la tarea'}",
        title_align="left",
        border_style="cyan",
    )
