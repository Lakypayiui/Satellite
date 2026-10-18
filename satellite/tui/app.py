"""TUI interactiva de Satellite (Textual), tipo opencode/hermes.

Toda la lógica vive en ``session.py``/``palette.py``; aquí sólo está la capa de
presentación e interacción.
"""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from satellite.approval.tui import TuiApproval
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.tui import palette, render
from satellite.tui.provider_screen import ProviderScreen
from satellite.tui.session import Session

PROMPT_HINT = "Escríbele al agente…   Enter enviar  ·  / comandos  ·  @ contexto  ·  Ctrl+P modelo"


def _rango_lineas(raw: str) -> tuple[int, int]:
    """``3`` o ``3-5`` → ``(inicio, fin)`` para el ancla de un comentario."""
    texto = (raw or "").strip()
    if not texto:
        raise ValueError("falta el rango de líneas (p. ej. 3 o 3-5)")
    partes = texto.split("-", 1)
    try:
        inicio = int(partes[0])
        fin = int(partes[1]) if len(partes) > 1 and partes[1] else inicio
    except ValueError as exc:
        raise ValueError("rango inválido: usa <inicio> o <inicio>-<fin>") from exc
    if inicio < 1 or fin < 1 or fin < inicio:
        raise ValueError("rango inválido: empieza en 1 y el fin no puede ser menor")
    return inicio, fin


class ApprovalScreen(ModalScreen[ApprovalDecision]):
    """Modal de aprobación humana (aprueba / pide cambios / rechaza).

    Para las peticiones de tipo ``path`` (acceso fuera del proyecto) ofrece además
    «Permitir la sesión», que recuerda esa carpeta hasta cerrar la TUI.
    """

    BINDINGS = [("escape", "reject", "Rechazar")]

    def __init__(
        self, proposal_id: str, summary: str, details: dict, kind: str = "agent"
    ) -> None:
        super().__init__()
        self.proposal_id = proposal_id
        self.summary = summary
        self.details = details
        self.kind = kind or "agent"

    def compose(self) -> ComposeResult:
        pide_permiso = self.kind in ("path", "command")
        with Container(id="approval-box"):
            with VerticalScroll(id="approval-scroll"):
                yield Static(
                    render.proposal_panel(self.summary, self.details, kind=self.kind),
                    id="approval-body",
                )
            with Horizontal(id="approval-actions"):
                yield Button(
                    "Permitir una vez" if pide_permiso else "Aprobar",
                    variant="success",
                    id="approve",
                )
                if pide_permiso:
                    yield Button("Permitir la sesión", variant="primary", id="approve-session")
                else:
                    yield Button("Cambios", variant="warning", id="changes")
                yield Button("Rechazar", variant="error", id="reject")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss(
                ApprovalDecision(status=ApprovalStatus.APPROVED, reason="aprobado en la TUI")
            )
        elif event.button.id == "approve-session":
            self.dismiss(
                ApprovalDecision(
                    status=ApprovalStatus.APPROVED,
                    reason="aprobado en la TUI",
                    feedback="session",
                )
            )
        elif event.button.id == "changes":
            self.dismiss(
                ApprovalDecision(status=ApprovalStatus.NEEDS_CHANGES, feedback="requiere cambios")
            )
        else:
            self.dismiss(
                ApprovalDecision(status=ApprovalStatus.REJECTED, reason="rechazado en la TUI")
            )

    def action_reject(self) -> None:
        self.dismiss(
            ApprovalDecision(status=ApprovalStatus.REJECTED, reason="rechazado en la TUI")
        )


class PlanOptionsScreen(ModalScreen[str]):
    """Modal del modo plan: elige una alternativa o «otra cosa».

    Devuelve el id de la opción elegida, o ``""`` para «otra cosa».
    """

    BINDINGS = [
        ("escape", "other", "Otra cosa"),
        ("o", "other", "Otra cosa"),
        ("1", "pick(1)", "Opción 1"),
        ("2", "pick(2)", "Opción 2"),
        ("3", "pick(3)", "Opción 3"),
        ("4", "pick(4)", "Opción 4"),
    ]

    def __init__(self, options) -> None:
        super().__init__()
        self.options = options

    def compose(self) -> ComposeResult:
        # Los botones van en vertical dentro de un contenedor con scroll: en una fila
        # no caben (cada uno mide >=16 columnas) y los de la derecha quedaban
        # recortados, así que algunas opciones no se podían elegir.
        with VerticalScroll(id="plan-box"):
            yield Static(render.plan_options_panel(self.options), id="approval-body")
            with Container(id="plan-actions"):
                for index, option in enumerate(self.options.options, 1):
                    label = f"{index}. {option.title}"
                    if len(label) > 40:
                        label = label[:37] + "…"
                    yield Button(label, id=f"opt-{option.id}")
                yield Button("Otra cosa…", variant="warning", id="opt-other")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "opt-other":
            self.dismiss("")
        elif button_id.startswith("opt-"):
            self.dismiss(button_id[len("opt-"):])
        else:
            self.dismiss("")

    def action_other(self) -> None:
        self.dismiss("")

    def action_pick(self, index: int) -> None:
        """Elegir por número, sin depender del foco ni del ratón."""
        options = list(getattr(self.options, "options", ()) or ())
        if 1 <= index <= len(options):
            self.dismiss(options[index - 1].id)


class SatelliteTUI(App):
    """Sesión interactiva sobre el runtime de Satellite."""

    # Ctrl+P se reserva para el selector de modelo (la paleta propia va en /help).
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen { background: #0f172a; color: #e2e8f0; }
    Header { background: #1e293b; color: #e2e8f0; }
    Footer { background: #1e293b; color: #94a3b8; }
    #main { height: 1fr; }
    #log {
        height: 1fr; width: 1fr; border: round #334155;
        background: #0f172a; padding: 0 1;
    }
    #agents-panel {
        width: 34; height: 1fr; display: none;
        border: round #334155; background: #0f172a; padding: 0 1;
    }
    .agent-card {
        border: round #334155; background: #1e293b;
        padding: 0 1; margin-bottom: 1; height: auto;
    }
    .agent-card.running { border: round #38bdf8; }
    .agent-card.done { border: round #22c55e; }
    .agent-card.failed { border: round #ef4444; }
    #statusbar { height: auto; padding: 0 1; background: #0f172a; }
    #prompt { border: round #38bdf8; background: #1e293b; color: #e2e8f0; }
    #prompt:focus { border: round #22c55e; }
    #approval-box {
        width: 78; height: auto; border: round #facc15;
        background: #1e293b; padding: 1 2;
    }
    #approval-actions { height: auto; align-horizontal: center; }
    #approval-actions Button { margin: 0 2; }
    #plan-box {
        width: 84; max-width: 95%; height: auto; max-height: 90%;
        border: round #facc15; background: #1e293b; padding: 1 2;
    }
    #plan-actions { height: auto; }
    #plan-actions Button { width: 1fr; margin-bottom: 1; }
    #approval-scroll { height: auto; max-height: 24; }
    #provider-box {
        width: 92; height: auto; max-height: 92%;
        border: round #38bdf8; background: #1e293b; padding: 1 2;
    }
    #provider-title { color: #e2e8f0; text-style: bold; }
    #provider-search { margin-bottom: 1; border: round #334155; background: #0f172a; }
    #provider-box Input { margin-bottom: 1; border: round #334155; background: #0f172a; }
    #provider-apps { height: 12; border: round #334155; background: #0f172a; }
    #provider-models { height: 8; border: round #334155; background: #0f172a; }
    #provider-actions { height: auto; align-horizontal: center; }
    #provider-actions Button { margin: 0 2; }
    #provider-status { height: auto; color: #94a3b8; }
    #apikey-box {
        width: 60; height: auto; border: round #38bdf8;
        background: #1e293b; padding: 1 2;
    }
    #apikey-title { color: #e2e8f0; text-style: bold; margin-bottom: 1; }
    #apikey-hint { color: #94a3b8; height: auto; }
    #apikey-actions { height: auto; align-horizontal: center; margin-top: 1; }
    #apikey-actions Button { margin: 0 2; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Salir"),
        ("ctrl+l", "clear_log", "Limpiar"),
        ("ctrl+p", "open_model", "Modelo"),
        ("f1", "show_help", "Ayuda"),
        ("escape", "focus_prompt", "Prompt"),
    ]

    def __init__(self, session: Session, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session = session
        self.approval = TuiApproval()
        self.transcript: list[str] = []
        self._subscribed = False
        self._pending_options = None
        self._chat_announced = False
        # Un turno a la vez: la cola sólo avanza entre turnos; nunca en paralelo.
        self._turn_active = False
        self._queue_running = False
        self._agents: dict[str, dict] = {}
        self._node_agent: dict[str, str] = {}
        self._cards: dict[str, Static] = {}

    # --- composición ---

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
            with VerticalScroll(id="agents-panel"):
                pass
        yield Static(id="statusbar")
        yield Input(placeholder=PROMPT_HINT, id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Satellite"
        self.sub_title = str(self.session.project_root)
        self._log(render.welcome(), plain="bienvenido")
        from satellite.context.instructions import load_instructions

        nombre_instrucciones, _texto = load_instructions(self.session.project_root)
        if nombre_instrucciones:
            self._log(
                Text(f"instrucciones del proyecto: {nombre_instrucciones}", style="dim"),
                plain=f"instrucciones={nombre_instrucciones}",
            )
        self._refresh_status()
        self.set_interval(0.4, self._poll_approvals)
        self.query_one("#prompt", Input).focus()

    # --- utilidades ---

    def _log(self, renderable, *, plain: str = "") -> None:
        self.query_one("#log", RichLog).write(renderable)
        self.transcript.append(plain or _stringify(renderable))

    def _refresh_status(self) -> None:
        try:
            status = self.session.status()
        except Exception as exc:  # noqa: BLE001
            self.query_one("#statusbar", Static).update(Text(f"error: {exc}", style="red"))
            return
        self.query_one("#statusbar", Static).update(render.status_bar(status))

    def _busy(self, active: bool, label: str = "trabajando…") -> None:
        prompt = self.query_one("#prompt", Input)
        prompt.placeholder = label if active else PROMPT_HINT

    def _ensure_subscribed(self) -> None:
        if self._subscribed:
            return
        try:
            self.session.bus().subscribe(self._on_event)
            self._subscribed = True
        except Exception:
            self._subscribed = False

    def _rearm_runtime(self) -> None:
        """Tras cambiar el workdir el bus es otro: se vuelve a suscribir la TUI."""
        self._subscribed = False
        self._ensure_subscribed()

    def _on_event(self, event) -> None:
        self._log(render.event_text(event), plain=event.event_type.value)
        self._track_agent(event)

    # --- panel de agentes ---

    def _show_agents(self, show: bool) -> None:
        """El panel solo ocupa sitio cuando hay agentes trabajando."""
        try:
            self.query_one("#agents-panel").display = show
        except Exception:  # noqa: BLE001 — la app puede estar desmontándose
            return

    def _agent_of(self, event) -> str:
        """A qué agente pertenece el evento (nodo → agente, o el id del payload)."""
        payload = getattr(event, "payload", {}) or {}
        node_id = str(getattr(event, "node_id", "") or "")
        if node_id and node_id in self._node_agent:
            return self._node_agent[node_id]
        for key in ("spec", "spec_id", "agent_id"):
            value = payload.get(key)
            if value:
                if node_id:
                    self._node_agent[node_id] = str(value)
                return str(value)
        return node_id or "satellite"

    def _upsert_agent(self, agent: str, *, status: str, action: str = "") -> None:
        state = self._agents.setdefault(agent, {"status": "idle", "action": ""})
        state["status"] = status
        if action:
            state["action"] = action
        self._sync_cards()
        self._show_agents(True)

    def _sync_cards(self) -> None:
        """Un rectángulo por agente, en el orden en que aparecieron."""
        try:
            panel = self.query_one("#agents-panel", VerticalScroll)
        except Exception:  # noqa: BLE001
            return
        for agent, state in self._agents.items():
            card = self._cards.get(agent)
            if card is None:
                card = Static(render.agent_card(agent, state["status"], state["action"]))
                card.add_class("agent-card")
                self._set_card_status(card, state["status"])
                self._cards[agent] = card
                panel.mount(card)
            else:
                card.update(render.agent_card(agent, state["status"], state["action"]))
                self._set_card_status(card, state["status"])

    @staticmethod
    def _set_card_status(card: Static, status: str) -> None:
        for name in ("running", "done", "failed", "idle"):
            card.set_class(status == name, name)

    def _clear_agents(self) -> None:
        for card in self._cards.values():
            card.remove()
        self._cards.clear()
        self._agents.clear()
        self._node_agent.clear()
        self._show_agents(False)

    def _track_agent(self, event) -> None:
        """Traduce los eventos del runtime en tarjetas de agente."""
        from satellite.core.events import EventType

        try:
            kind = getattr(event, "event_type", None)
            payload = getattr(event, "payload", {}) or {}
            if kind is EventType.TASK_STARTED:
                self._clear_agents()
            elif kind is EventType.NODE_STARTED:
                self._upsert_agent(self._agent_of(event), status="running", action="razonando")
            elif kind is EventType.TOOL_CALLED:
                tool = str(getattr(event, "tool_name", "") or payload.get("tool", ""))
                if tool:
                    accion = render.tool_action(tool, payload.get("arguments"))
                    self._upsert_agent(
                        self._agent_of(event), status="running", action=accion
                    )
            elif kind is EventType.NODE_FINISHED:
                failed = "fail" in str(payload.get("status", "")).lower()
                self._upsert_agent(
                    self._agent_of(event),
                    status="failed" if failed else "done",
                    action=str(payload.get("summary", "") or ""),
                )
            elif kind is EventType.DELEGATION_STARTED:
                self._upsert_agent(
                    self._agent_of(event), status="running", action="recibiendo delegación"
                )
            elif kind is EventType.DELEGATION_COMPLETED:
                self._upsert_agent(
                    self._agent_of(event),
                    status="done" if payload.get("ok", True) else "failed",
                    action=str(payload.get("summary", "") or ""),
                )
            elif kind in (EventType.AGENT_CREATED, EventType.AGENT_EXTENDED):
                label = "creado" if kind is EventType.AGENT_CREATED else "extendido"
                self._upsert_agent(self._agent_of(event), status="done", action=label)
            elif kind is EventType.TASK_FINISHED:
                for state in self._agents.values():
                    if state["status"] == "running":
                        state["status"] = "done"
                self._sync_cards()
                if all(state["status"] != "running" for state in self._agents.values()):
                    self._show_agents(False)
                self._log_changes(getattr(event, "task_id", ""))
        except Exception:  # noqa: BLE001 — el panel nunca debe tumbar la sesión
            return

    # --- entrada ---

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Con un modal abierto (p. ej. el selector de proveedor) el Enter es del modal:
        # no debe llegar aquí y ejecutarse como comando.
        if isinstance(self.screen, ModalScreen):
            return
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        name, arg = palette.parse(line)
        if not name:
            return
        if name == "run":
            self._log(render.user_card(line), plain=f"› {line}")
        else:
            self._log(Text.assemble(("› ", "bold green"), (line, "bold")))
        handler = getattr(self, f"_cmd_{name.replace('-', '_')}", None)
        if handler is None:
            self._log(Text(f"comando desconocido: /{name}  (usa /help)", style="red"))
            return
        try:
            handler(arg)
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error: {exc}", style="red"))

    # --- aprobación ---

    def _poll_approvals(self) -> None:
        if isinstance(self.screen, ApprovalScreen):
            return
        pending = self.approval.pending()
        if not pending:
            return
        item = pending[0]
        self.push_screen(
            ApprovalScreen(
                item.get("proposal_id", ""),
                item.get("summary", ""),
                item.get("details", {}),
                item.get("kind", "agent"),
            ),
            lambda decision, pid=item.get("proposal_id", ""): self._resolve_approval(pid, decision),
        )

    def _resolve_approval(self, proposal_id: str, decision) -> None:
        if decision is None:
            decision = ApprovalDecision(
                status=ApprovalStatus.REJECTED, reason="cerrado sin decisión"
            )
        self.approval.submit(proposal_id, decision)
        self._log(
            Text(f"decisión: {decision.status.value}", style="bold"),
            plain=f"decision {decision.status.value}",
        )

    # --- acciones (workers) ---

    def _spawn(self, coro, *, name: str) -> None:
        self._ensure_subscribed()
        self.run_worker(coro, name=name, group="runtime", exclusive=False)

    def _approval_for_session(self):
        """Puerto de aprobación de esta sesión.

        En ``unrestricted`` no se pregunta nada (el usuario pidió no tener límites);
        en ``ask`` y en ``auto`` sí se pregunta por las rutas de fuera del proyecto:
        salir del proyecto no se concede en silencio. Las zonas protegidas las veta el
        guard y no pasan por aquí.
        """
        from satellite.approval.base import AutoApprovalPort

        try:
            mode = self.session.status().policy_mode
        except Exception:  # noqa: BLE001 — si no se puede leer, preguntar es lo seguro
            mode = "ask"
        return AutoApprovalPort() if mode == "unrestricted" else self.approval

    async def _do_chat(
        self, text: str, *, plan_mode: bool = False, extra_metadata: dict | None = None
    ) -> None:
        from satellite.context.mentions import parse as parse_mentions

        mencionadas = parse_mentions(text, self.session.project_root)
        if mencionadas.refs or mencionadas.missing:
            resumen = mencionadas.summary()
            self._log(
                Text(f"contexto: {resumen}", style="dim"), plain=f"contexto: {resumen}"
            )
        self._turn_active = True
        self._busy(True, "el agente está trabajando…")
        inicio = time.perf_counter()
        try:
            reply = await self.session.send_message(
                text,
                approval=self._approval_for_session(),
                plan_mode=plan_mode,
                extra_metadata=extra_metadata,
            )
            elapsed_ms = (time.perf_counter() - inicio) * 1000
            if not self._chat_announced:
                self._chat_announced = True
                restored = self.session.restored_turns()
                if restored:
                    self._log(
                        Text(
                            f"conversación recuperada: {restored} turnos "
                            "(usa /new para empezar limpia)"
                        ),
                        plain=f"restaurados {restored}",
                    )
            self._log(
                render.reply_panel(
                    reply, elapsed_ms=elapsed_ms, metrics=getattr(reply, "metrics", None)
                ),
                plain=f"respuesta {reply.status}",
            )
            if reply.compaction and reply.compaction.get("dropped"):
                self._log(
                    Text(
                        f"contexto compactado automáticamente: "
                        f"{reply.compaction['dropped']} turnos → resumen "
                        f"({reply.compaction.get('method', '')})"
                    ),
                    plain="compactado-auto",
                )
            if (reply.output or {}).get("mode") == "plan_options":
                self._open_plan_options(reply.output.get("plan_options") or {})
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error: {exc}", style="red"))
        finally:
            self._turn_active = False
            self._busy(False)
            self._refresh_status()

    def _cmd_compact(self, arg: str) -> None:
        """Compacta el contexto de la conversación a mano."""
        self._spawn(self._do_compact(), name="compact")

    async def _do_compact(self) -> None:
        self._busy(True, "compactando contexto…")
        try:
            result = await self.session.compact_context()
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error al compactar: {exc}", style="red"))
            return
        finally:
            self._busy(False)
            self._refresh_status()
        if result is None or not getattr(result, "dropped", 0):
            reason = (
                "compactar no reduciría el contexto (conversación pequeña)"
                if getattr(result, "method", "") == "sin-efecto"
                else "no hacía falta compactar (conversación corta)"
            )
            self._log(Text(reason), plain="compact")
            return
        self._log(
            Text(
                f"contexto compactado: {result.dropped} turnos → resumen ({result.method}), "
                f"~{result.tokens_before} → ~{result.tokens_after} tokens"
            ),
            plain=f"compactado {result.dropped}",
        )

    def _open_plan_options(self, payload: dict) -> None:
        """Muestra las alternativas y abre el modal de elección."""
        from satellite.core.plan import PlanOptions

        try:
            options = PlanOptions.from_dict(payload)
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"alternativas inválidas: {exc}", style="red"))
            return
        self._log(render.plan_options_panel(options), plain=f"opciones={len(options.options)}")
        self._pending_options = options
        self.push_screen(PlanOptionsScreen(options), self._on_plan_choice)

    def _on_plan_choice(self, option_id: str | None) -> None:
        """Continúa con la alternativa elegida (o pide el enfoque propio)."""
        options = getattr(self, "_pending_options", None)
        if not option_id:
            self._log(
                Text("describe tu propio enfoque y envíalo como mensaje", style="italic")
            )
            return
        option = None
        if options is not None:
            option = options.option(option_id)
        if option is None:
            self._log(Text(f"alternativa desconocida: {option_id}", style="red"))
            return
        self._log(Text(f"opción elegida: {option.title}", style="bold green"), plain=f"opción {option.id}")
        self._spawn(
            self._do_chat_option(option), name="plan-choice"
        )

    async def _do_chat_option(self, option) -> None:
        self._busy(True, "el agente está trabajando…")
        inicio = time.perf_counter()
        try:
            reply = await self.session.choose_option(option, approval=self.approval)
            elapsed_ms = (time.perf_counter() - inicio) * 1000
            self._log(
                render.reply_panel(
                    reply, elapsed_ms=elapsed_ms, metrics=getattr(reply, "metrics", None)
                ),
                plain=f"respuesta {reply.status}",
            )
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error: {exc}", style="red"))
        finally:
            self._busy(False)
            self._refresh_status()

    async def _do_create(self, need: str) -> None:
        self._busy(True, "creando agente…")
        try:
            outcome = await self.session.create_agent(need, approval=self.approval)
            detail = (
                outcome.spec.id
                if outcome.spec is not None
                else outcome.reason or outcome.question or outcome.reuse_candidate or ""
            )
            style = "green" if outcome.kind in ("created", "reuse") else "yellow"
            self._log(Text(f"{outcome.kind}: {detail}", style=style), plain=f"create {outcome.kind}")
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error: {exc}", style="red"))
        finally:
            self._busy(False)
            self._refresh_status()

    async def _do_curate(self, request: str) -> None:
        self._busy(True, "curando skills…")
        try:
            outcome = await self.session.curate(request, approval=self.approval)
            summary = f"{outcome.kind}: aplicadas={len(outcome.applied)} omitidas={len(outcome.skipped)}"
            self._log(Text(summary, style="green"), plain=f"curate {outcome.kind}")
            for label in outcome.applied:
                self._log(Text(f"  ✓ {label}", style="green"), plain=f"applied {label}")
            for label in outcome.skipped:
                self._log(Text(f"  ✗ {label}", style="dim"), plain=f"skipped {label}")
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error: {exc}", style="red"))
        finally:
            self._busy(False)

    async def _do_status(self, task_id: str) -> None:
        from satellite.persistence.store import FileStore

        try:
            events = await FileStore(self.session.project_root).load_events(task_id)
            self._log(Text(f"tarea {task_id}: {len(events)} eventos"), plain=f"status {task_id}")
            for event in events[-20:]:
                self._log(render.event_text(event), plain=event.event_type.value)
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error: {exc}", style="red"))

    async def _do_inspect(self, task_id: str) -> None:
        from satellite.persistence.store import FileStore

        try:
            graph = await FileStore(self.session.project_root).load_graph(task_id)
            self._log(Text(json.dumps(graph.to_dict(), indent=2, ensure_ascii=False)), plain=f"graph {task_id}")
        except Exception as exc:  # noqa: BLE001
            self._log(Text(f"error: {exc}", style="red"))

    # --- comandos sincrónicos ---

    def _turn_en_curso(self) -> bool:
        """True (y avisa) si ya hay un turno o un drenado de cola en marcha."""
        if self._turn_active or self._queue_running:
            texto = "turno en curso: usa /queue add <texto> para la siguiente instrucción"
            self._log(Text(texto, style="yellow"), plain="turno en curso")
            return True
        return False

    def _cmd_run(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /run <mensaje>", style="yellow"))
            return
        if self._turn_en_curso():
            return
        self._spawn(self._do_chat(arg.strip()), name="chat")

    def _cmd_plan(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /plan <tarea ambigua o a planificar>", style="yellow"))
            return
        if self._turn_en_curso():
            return
        self._spawn(self._do_chat(arg.strip(), plan_mode=True), name="plan")

    def _cmd_new(self, arg: str) -> None:
        self.session.new_conversation()
        self._log(Text("conversación nueva", style="green"), plain="new")

    def _cmd_history(self, arg: str) -> None:
        turns = self.session.conversation()
        self._log(render.conversation_table(turns), plain=f"history={len(turns)}")

    # --- comentarios inline (TUI: ancla explícita, sin diff visual) ---

    def _active_session_id(self) -> str:
        from satellite.orchestration.sessions import SessionStore

        return SessionStore(self.session.project_root).active_id()

    def _current_task_id(self) -> str:
        """Tarea del último turno con ``task_id`` (para vincular el comentario)."""
        for turno in reversed(self.session.conversation()):
            task_id = str(getattr(turno, "task_id", "") or "")
            if task_id:
                return task_id
        return ""

    def _cmd_comments(self, arg: str) -> None:
        """``/comments [list|add|resolve|reopen|delete]`` con archivo + lado + rango."""
        from satellite import comments

        root = self.session.project_root
        partes = arg.strip().split(" ", 1)
        accion = partes[0].lower() if partes and partes[0] else ""
        resto = partes[1].strip() if len(partes) > 1 else ""

        if accion in ("", "list"):
            items = comments.list_comments(root)
            if not items:
                self._log(Text("(sin comentarios)", style="dim"), plain="comments=0")
                return
            lineas = []
            for item in items:
                rango = (
                    f"{item['line_start']}-{item['line_end']}"
                    if item["line_end"] != item["line_start"]
                    else str(item["line_start"])
                )
                estado = "resuelto" if item["resolved"] else "abierto"
                lineas.append(
                    f"{item['id']}  {item['file']}:{rango}  [{estado}] [{item['side']}]  {item['text']}"
                )
            self._log(Text("\n".join(lineas)), plain=f"comments={len(lineas)}")
            return

        if accion == "add":
            side = "new"
            if resto.startswith("--old "):
                side = "old"
                resto = resto[len("--old ") :].strip()
            trozos = resto.split(" ", 2)
            if len(trozos) < 3:
                self._log(
                    Text(
                        "uso: /comments add [--old] <archivo> <inicio|inicio-fin> <texto>",
                        style="yellow",
                    ),
                    plain="uso: /comments add",
                )
                return
            archivo, rango, texto = trozos
            try:
                inicio, fin = _rango_lineas(rango)
                item = comments.add_comment(
                    root,
                    file=archivo,
                    line_start=inicio,
                    line_end=fin,
                    text=texto,
                    side=side,
                    session_id=self._active_session_id(),
                    task_id=self._current_task_id(),
                )
            except ValueError as exc:
                self._log(Text(f"error: {exc}", style="red"), plain=f"error: {exc}")
                return
            self._log(
                Text(
                    f"comentario {item['id']}: {item['file']}:{item['line_start']}-{item['line_end']}",
                    style="green",
                ),
                plain=f"comentario {item['id']}",
            )
            self._cmd_comments("list")
            return

        if accion in ("resolve", "reopen"):
            if not resto:
                self._log(
                    Text(f"uso: /comments {accion} <id>", style="yellow"),
                    plain=f"uso: /comments {accion}",
                )
                return
            try:
                comments.update_comment(root, resto, resolved=(accion == "resolve"))
            except KeyError:
                self._log(
                    Text(f"no existe el comentario {resto}", style="yellow"),
                    plain="comentario no existe",
                )
                return
            except ValueError as exc:
                self._log(Text(f"error: {exc}", style="red"), plain=f"error: {exc}")
                return
            self._log(
                Text(
                    f"comentario {resto}: {'resuelto' if accion == 'resolve' else 'reabierto'}",
                    style="green",
                ),
                plain=f"comentario {accion}",
            )
            self._cmd_comments("list")
            return

        if accion == "delete":
            if not resto:
                self._log(
                    Text("uso: /comments delete <id>", style="yellow"),
                    plain="uso: /comments delete",
                )
                return
            if not comments.delete_comment(root, resto):
                self._log(
                    Text(f"no existe el comentario {resto}", style="yellow"),
                    plain="comentario no existe",
                )
                return
            self._log(Text(f"comentario {resto} borrado", style="green"), plain="comentario borrado")
            self._cmd_comments("list")
            return

        self._log(
            Text(
                "uso: /comments [list | add [--old] <archivo> <rango> <texto> | "
                "resolve|reopen|delete <id>]",
                style="yellow",
            ),
            plain="uso: /comments",
        )

    # --- cola de instrucciones (siguiente turno, nunca in-flight) ---

    def _cmd_queue(self, arg: str) -> None:
        """``/queue [list|add|edit|remove|clear|run]``."""
        from satellite import prompt_queue

        root = self.session.project_root
        partes = arg.strip().split(" ", 1)
        accion = partes[0].lower() if partes and partes[0] else ""
        resto = partes[1].strip() if len(partes) > 1 else ""

        if accion in ("", "list"):
            items = prompt_queue.list_items(root)
            if not items:
                self._log(Text("(cola vacía)", style="dim"), plain="cola=0")
                return
            lineas = [
                f"{indice}. {item['id']}  {item['text']}"
                + (" [plan]" if item["plan_mode"] else "")
                for indice, item in enumerate(items, 1)
            ]
            self._log(Text("\n".join(lineas)), plain=f"cola={len(lineas)}")
            return

        if accion == "add":
            plan_mode = False
            if resto.startswith("--plan "):
                plan_mode = True
                resto = resto[len("--plan ") :].strip()
            if not resto:
                self._log(
                    Text("uso: /queue add [--plan] <texto>", style="yellow"),
                    plain="uso: /queue add",
                )
                return
            try:
                prompt_queue.enqueue(root, resto, plan_mode=plan_mode)
            except ValueError as exc:
                self._log(Text(f"error: {exc}", style="red"), plain=f"error: {exc}")
                return
            self._log(Text(f"encolado: {resto}", style="green"), plain="encolado")
            self._cmd_queue("list")
            return

        if accion == "edit":
            item_id, _, texto = resto.partition(" ")
            if not item_id or not texto.strip():
                self._log(
                    Text("uso: /queue edit <id> <texto>", style="yellow"),
                    plain="uso: /queue edit",
                )
                return
            try:
                prompt_queue.update_item(root, item_id, text=texto.strip())
            except KeyError:
                self._log(
                    Text(f"no existe el item {item_id}", style="yellow"),
                    plain="item no existe",
                )
                return
            except ValueError as exc:
                self._log(Text(f"error: {exc}", style="red"), plain=f"error: {exc}")
                return
            self._log(
                Text(f"editado: {item_id} (afecta al turno futuro)", style="green"),
                plain="cola editada",
            )
            self._cmd_queue("list")
            return

        if accion in ("remove", "rm"):
            if not resto:
                self._log(
                    Text("uso: /queue remove <id>", style="yellow"),
                    plain="uso: /queue remove",
                )
                return
            try:
                prompt_queue.remove_item(root, resto)
            except KeyError:
                self._log(
                    Text(f"no existe el item {resto}", style="yellow"),
                    plain="item no existe",
                )
                return
            self._log(Text(f"quitado: {resto}", style="green"), plain="cola quitada")
            self._cmd_queue("list")
            return

        if accion == "clear":
            prompt_queue.clear(root)
            self._log(Text("cola vacía", style="green"), plain="cola=0")
            return

        if accion == "run":
            self._run_queue()
            return

        self._log(
            Text(
                "uso: /queue [list | add [--plan] <texto> | edit <id> <texto> | "
                "remove <id> | clear | run]",
                style="yellow",
            ),
            plain="uso: /queue",
        )

    def _run_queue(self) -> None:
        """Arranca el drenado de la cola (un prompt por turno), si no hay otro en curso."""
        from satellite import prompt_queue

        if self._queue_running:
            self._log(Text("la cola ya se está ejecutando", style="yellow"), plain="cola ya en marcha")
            return
        if self._turn_active:
            self._log(
                Text(
                    "turno en curso: espera a que termine (o /cancel) antes de /queue run",
                    style="yellow",
                ),
                plain="turno en curso",
            )
            return
        if not prompt_queue.list_items(self.session.project_root):
            self._log(Text("(cola vacía)", style="dim"), plain="cola=0")
            return
        # Se marca antes de programar el worker: dos /queue run seguidos no duplican el drenado.
        self._queue_running = True
        try:
            self._spawn(self._do_run_queue(), name="queue")
        except Exception:
            self._queue_running = False
            raise

    async def _do_run_queue(self) -> None:
        """Ejecuta la cola en orden reutilizando ``_do_chat`` (el flujo real de envío)."""
        from satellite import prompt_queue

        try:
            while True:
                item = prompt_queue.pop_next(self.session.project_root)
                if item is None:
                    break
                self._log(
                    Text(f"cola › {item['text']}", style="dim"),
                    plain=f"cola: {item['text']}",
                )
                await self._do_chat(item["text"], plan_mode=bool(item.get("plan_mode")))
        finally:
            self._queue_running = False
            self._refresh_status()

    def _cmd_sessions(self, arg: str) -> None:
        """Lista las conversaciones guardadas (activa ●, fijada ★, archivada)."""
        infos = self.session.sessions()
        if not infos:
            self._log(Text("(sin sesiones guardadas)", style="dim"), plain="sessions=0")
            return
        lines = []
        for info in infos:
            marcas = []
            if info.pinned:
                marcas.append("★")
            if info.archived:
                marcas.append("archivada")
            sufijo = f"  [{' '.join(marcas)}]" if marcas else ""
            lines.append(
                f" {'●' if info.active else ' '} {info.id}  {info.name}  "
                f"({info.turns} turnos){sufijo}"
            )
        self._log(Text("\n".join(lines)), plain=f"sessions={len(infos)}")

    def _cmd_session(self, arg: str) -> None:
        """``/session new|use|name|pin|unpin|archive|unarchive [id|nombre]``."""
        partes = arg.strip().split(" ", 1)
        accion = partes[0].lower() if partes and partes[0] else ""
        resto = partes[1].strip() if len(partes) > 1 else ""
        if accion == "new":
            session_id = self.session.new_session(resto)
            self._log(
                Text(f"sesión nueva: {session_id}", style="green"),
                plain=f"sesión nueva: {session_id}",
            )
            return
        if accion == "use":
            encontrada = self.session.use_session(resto) if resto else None
            if encontrada is None:
                self._log(
                    Text("no existe esa sesión (mira /sessions)", style="yellow"),
                    plain="no existe esa sesión (mira /sessions)",
                )
                return
            self._log(
                Text(f"sesión activa: {encontrada}", style="green"),
                plain=f"sesión activa: {encontrada}",
            )
            return
        if accion == "name":
            if not resto:
                self._log(
                    Text("uso: /session name <nombre>", style="yellow"),
                    plain="uso: /session name <nombre>",
                )
                return
            self.session.rename_session(resto)
            self._log(
                Text(f"sesión renombrada: {resto}", style="green"),
                plain=f"sesión renombrada: {resto}",
            )
            return
        if accion in ("pin", "unpin"):
            if not resto:
                self._log(
                    Text("uso: /session pin|unpin <id|nombre>", style="yellow"),
                    plain="uso: /session pin|unpin <id|nombre>",
                )
                return
            session_id = self.session.pin_session(resto, accion == "pin")
            if session_id is None:
                self._log(
                    Text("no existe esa sesión (mira /sessions)", style="yellow"),
                    plain="no existe esa sesión (mira /sessions)",
                )
                return
            hecho = "fijada" if accion == "pin" else "desfijada"
            self._log(
                Text(f"sesión {hecho}: {session_id}", style="green"),
                plain=f"sesión {hecho}: {session_id}",
            )
            self._cmd_sessions("")  # refresca el listado
            return
        if accion in ("archive", "unarchive"):
            if not resto:
                self._log(
                    Text("uso: /session archive|unarchive <id|nombre>", style="yellow"),
                    plain="uso: /session archive|unarchive <id|nombre>",
                )
                return
            session_id = self.session.archive_session(resto, accion == "archive")
            if session_id is None:
                self._log(
                    Text("no existe esa sesión (mira /sessions)", style="yellow"),
                    plain="no existe esa sesión (mira /sessions)",
                )
                return
            hecho = "archivada" if accion == "archive" else "desarchivada"
            self._log(
                Text(f"sesión {hecho}: {session_id}", style="green"),
                plain=f"sesión {hecho}: {session_id}",
            )
            self._cmd_sessions("")  # refresca el listado (activa incluida)
            return
        self._log(
            Text(
                "uso: /session <new [nombre] | use <id|nombre> | name <nombre> | "
                "pin|unpin <id|nombre> | archive|unarchive <id|nombre>>",
                style="yellow",
            ),
            plain=(
                "uso: /session <new [nombre] | use <id|nombre> | name <nombre> | "
                "pin|unpin <id|nombre> | archive|unarchive <id|nombre>>"
            ),
        )

    def _cmd_git(self, arg: str) -> None:
        """Estado real del repo en el workdir activo (sólo lectura)."""
        from satellite import gitops

        root = self.session.project_root
        objetivo = gitops.active_workdir(root) or root
        try:
            data = gitops.status(objetivo)
        except gitops.GitError as exc:
            self._log(Text(str(exc), style="yellow"), plain="git: sin repositorio")
            return
        lineas = [f"rama: {data['branch']}" + (" (detached)" if data["detached"] else "")]
        if objetivo != root:
            lineas.append(f"workdir: {objetivo}")
        if data["upstream"]:
            lineas.append(f"upstream: {data['upstream']} ↑{data['ahead']} ↓{data['behind']}")
        if data["clean"]:
            lineas.append("sin cambios")
        for grupo, titulo in (
            ("conflicts", "conflicto"),
            ("staged", "staged"),
            ("unstaged", "sin preparar"),
            ("untracked", "sin seguimiento"),
        ):
            for entry in data[grupo]:
                lineas.append(f"  [{titulo}] {entry['code']} {entry['path']}")
        self._log(
            Text("\n".join(lineas)),
            plain=f"git={data['branch']} files={len(data['files'])}",
        )

    def _cmd_worktrees(self, arg: str) -> None:
        """``/worktrees [list|add|use|remove]``: contexto de ejecución del proyecto."""
        from satellite import gitops

        root = self.session.project_root
        if not gitops.is_repo(root):
            self._log(Text("no es un repositorio git", style="yellow"), plain="worktrees=0")
            return

        partes = arg.strip().split(" ", 1)
        accion = partes[0].lower() if partes and partes[0] else ""
        resto = partes[1].strip() if len(partes) > 1 else ""

        if accion == "add":
            if not resto:
                self._log(
                    Text("uso: /worktrees add <nombre>", style="yellow"),
                    plain="uso: /worktrees add",
                )
                return
            try:
                creado = gitops.add_worktree(root, resto)
            except gitops.GitError as exc:
                self._log(Text(str(exc), style="yellow"), plain="worktree error")
                return
            self._log(
                Text(
                    f"worktree creado: {creado['path']} (rama {creado['branch']})",
                    style="green",
                ),
                plain=f"worktree {creado['branch']}",
            )
            self._cmd_worktrees("")
            return

        if accion in ("use", "activate"):
            objetivo = "" if resto.lower() in ("proyecto", "project", "") else resto
            try:
                efectivo = self.session.use_workdir(objetivo or None)
            except gitops.GitError as exc:
                self._log(Text(str(exc), style="yellow"), plain="worktree error")
                return
            self._log(
                Text(f"ejecutando en: {efectivo or root}", style="green"),
                plain=f"workdir={efectivo or root}",
            )
            self._rearm_runtime()
            self._refresh_status()
            return

        if accion == "remove":
            force = resto.strip().endswith("--force")
            path = resto.strip()
            if force:
                path = path[: -len("--force")].strip()
            if not path:
                self._log(
                    Text("uso: /worktrees remove <ruta> [--force]", style="yellow"),
                    plain="uso: /worktrees remove",
                )
                return
            activo_antes = gitops.active_workdir(root)
            try:
                quitado = gitops.remove_worktree(root, path, force=force)
            except gitops.GitError as exc:
                self._log(Text(str(exc), style="yellow"), plain="worktree error")
                return
            removido = Path(str(quitado.get("removed", ""))).resolve()
            if activo_antes is not None and activo_antes == removido:
                self.session.use_workdir(None)
                self._rearm_runtime()
            self._log(
                Text(f"worktree quitado: {quitado.get('removed', path)}", style="green"),
                plain="worktree quitado",
            )
            self._refresh_status()
            return

        if accion not in ("", "list"):
            self._log(
                Text(
                    "uso: /worktrees [list | add <nombre> | use <ruta|proyecto> | "
                    "remove <ruta> [--force]]",
                    style="yellow",
                ),
                plain="uso: /worktrees",
            )
            return

        activo = gitops.active_workdir(root)
        lineas = []
        for wt in gitops.list_worktrees(root):
            marcas = []
            if wt.get("main"):
                marcas.append("principal")
            if activo and str(activo) == wt["path"]:
                marcas.append("activo")
            if wt.get("dirty"):
                marcas.append("con cambios")
            sufijo = f"  [{', '.join(marcas)}]" if marcas else ""
            lineas.append(f"{wt['branch'] or '(detached)'}{sufijo}  {wt['path']}")
        self._log(
            Text("\n".join(lineas) or "(sin worktrees)"),
            plain=f"worktrees={len(lineas)}",
        )

    def _cmd_jobs(self, arg: str) -> None:
        """``/jobs`` lista; ``output``, ``wait`` y ``stop`` trabajan con un id."""
        from satellite.tools.jobs import registry_for

        partes = arg.strip().split(" ", 1)
        accion = partes[0].lower() if partes and partes[0] else ""
        resto = partes[1].strip() if len(partes) > 1 else ""
        registry = registry_for(self.session.project_root)
        if accion in ("", "list"):
            infos = registry.list()
            if not infos:
                texto = "(no hay trabajos en segundo plano)"
                self._log(Text(texto, style="dim"), plain=texto)
                return
            texto = "\n".join(
                f" {info.id}  {info.status:12}  {info.command}" for info in infos
            )
            self._log(Text(texto), plain=texto)
            return
        if accion == "output":
            if not resto:
                self._log(Text("uso: /jobs output <id>", style="yellow"), plain="uso: /jobs output <id>")
                return
            info = registry.status(resto)
            if info is None:
                texto = f"no existe el trabajo '{resto}'"
                self._log(Text(texto, style="yellow"), plain=texto)
                return
            self._log(
                Text(f"trabajo {resto} ({info.status}):\n{registry.output(resto)}"),
                plain=registry.output(resto),
            )
            return
        if accion == "wait":
            if not resto:
                texto = "uso: /jobs wait <id> [segundos]"
                self._log(Text(texto, style="yellow"), plain=texto)
                return
            trozos = resto.split()
            try:
                timeout_s = float(trozos[1]) if len(trozos) > 1 else 30.0
            except ValueError:
                timeout_s = 30.0
            self._spawn(self._do_job_wait(trozos[0], timeout_s), name="jobs")
            return
        if accion == "stop":
            if not resto:
                self._log(Text("uso: /jobs stop <id>", style="yellow"), plain="uso: /jobs stop <id>")
                return
            info = registry.stop(resto)
            if info is None:
                texto = f"no puedo parar '{resto}': lo lanzó otro proceso"
            elif info.status == "stopped":
                texto = f"trabajo {info.id} parado"
            else:
                texto = f"trabajo {info.id} ya había terminado ({info.status})"
            self._log(Text(texto, style="green"), plain=texto)
            return
        texto = "uso: /jobs [list | output <id> | wait <id> [s] | stop <id>]"
        self._log(Text(texto, style="yellow"), plain=texto)

    def _cmd_cancel(self, arg: str) -> None:
        """Pide cancelar el turno en curso (cooperativa: se para entre nodos)."""
        try:
            orquestador = self.session.runtime()[0]
        except Exception as exc:  # noqa: BLE001 — sin runtime no hay a quién pedírselo
            self._log(
                Text(f"no se pudo cancelar: {exc}", style="yellow"),
                plain=f"no se pudo cancelar: {exc}",
            )
            return
        cancel = getattr(orquestador, "cancel", None)
        if not callable(cancel):
            self._log(Text("este runtime no sabe cancelar", style="yellow"), plain="sin cancelación")
            return
        motivo = arg.strip() or "cancelado desde la TUI"
        resultado = cancel("", motivo)
        if not resultado.get("cancelled"):
            self._log(
                Text("no se pudo pedir la cancelación", style="yellow"),
                plain="cancelación no pedida",
            )
            return
        self._log(
            Text("cancelación pedida: se para entre nodos, lo hecho queda", style="green"),
            plain="cancelación pedida",
        )

    async def _do_job_wait(self, job_id: str, timeout_s: float) -> None:
        """Espera a que termine un trabajo sin bloquear la TUI."""
        from satellite.tools.jobs import registry_for

        registry = registry_for(self.session.project_root)
        info, estado = await registry.wait(job_id, timeout_s)
        if info is None:
            texto = f"no existe el trabajo '{job_id}'"
        elif estado == "ajeno":
            texto = f"no puedo esperar '{job_id}': lo lanzó otro proceso"
        elif estado == "timeout":
            texto = f"trabajo {job_id} sigue en curso tras {timeout_s:.0f}s"
        else:
            texto = f"trabajo {job_id} terminó: {info.status}"
        self._log(Text(texto, style="green"), plain=texto)

    def _cmd_create(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /create <necesidad de capacidad>", style="yellow"))
            return
        self._spawn(self._do_create(arg.strip()), name="create")

    def _cmd_curate(self, arg: str) -> None:
        self._spawn(self._do_curate(arg.strip()), name="curate")

    def _cmd_status(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /status <task_id>", style="yellow"))
            return
        self._spawn(self._do_status(arg.strip()), name="status")

    def _cmd_inspect(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /inspect <task_id>", style="yellow"))
            return
        self._spawn(self._do_inspect(arg.strip()), name="inspect")

    def _cmd_help(self, arg: str) -> None:
        self._log(Text(palette.help_text()), plain=palette.help_text())

    def _cmd_agents(self, arg: str) -> None:
        specs = self.session.agents()
        self._log(render.agent_table(specs), plain=f"agents={len(specs)}")

    def _cmd_skills(self, arg: str) -> None:
        manifests = self.session.skills()
        self._log(render.skill_table(manifests), plain=f"skills={len(manifests)}")

    def _cmd_skill_search(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /skill-search <texto>", style="yellow"))
            return
        hits = self.session.skill_search(arg.strip())
        self._log(render.search_table(hits), plain=f"search={len(hits)}")

    def _cmd_skill_edit(self, arg: str) -> None:
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = arg.split()
        if not parts:
            self._log(Text("uso: /skill-edit <id> [--description <t>] [--tags a,b]", style="yellow"))
            return
        skill_id = parts[0]
        description = None
        tags = None
        index = 1
        while index < len(parts):
            token = parts[index]
            if token == "--description" and index + 1 < len(parts):
                description = parts[index + 1]
                index += 2
            elif token == "--tags" and index + 1 < len(parts):
                tags = [part.strip() for part in parts[index + 1].split(",") if part.strip()]
                index += 2
            else:
                index += 1
        manifest = self.session.skill_edit(skill_id, description=description, tags=tags)
        self._log(Text(f"editada: {manifest.id}", style="green"), plain=f"editada {manifest.id}")

    def _cmd_skill_delete(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /skill-delete <id>", style="yellow"))
            return
        skill_id = arg.strip().split()[0]
        self.session.skill_delete(skill_id)
        self._log(Text(f"borrada: {skill_id}", style="green"), plain=f"borrada {skill_id}")

    def _cmd_tasks(self, arg: str) -> None:
        tasks = self.session.tasks()
        self._log(Text("\n".join(tasks) if tasks else "(sin tareas)"), plain=f"tasks={len(tasks)}")

    def _cmd_snapshots(self, arg: str) -> None:
        snapshots = self.session.snapshots()
        self._log(
            Text("\n".join(snapshots) if snapshots else "(sin snapshots)"),
            plain=f"snapshots={len(snapshots)}",
        )

    def _cmd_changes(self, arg: str) -> None:
        """Archivos y diff de lo que cambió una tarea (la última, si no se dice cuál)."""
        from satellite.persistence.changes import ChangeLedger

        ledger = ChangeLedger(self.session.project_root)
        task_id = arg.strip() or ledger.latest_task() or ""
        if not task_id or not ledger.has(task_id):
            self._log(
                Text("(no hay cambios registrados de esa tarea)", style="dim"),
                plain="(no hay cambios registrados de esa tarea)",
            )
            return
        files = ledger.files(task_id)
        self._log(
            render.changes_panel(
                task_id,
                files,
                ledger.diff(task_id),
                limit=render.CHANGES_DIFF_LINES,
                hint="/changes muestra el diff completo",
            ),
            plain=f"changes={task_id} archivos={len(files)}: {', '.join(files)}",
        )

    def _cmd_undo_task(self, arg: str) -> None:
        """Devuelve los archivos de una tarea a como estaban antes de esa tarea."""
        from satellite.persistence.changes import ChangeLedger

        ledger = ChangeLedger(self.session.project_root)
        task_id = arg.strip() or ledger.latest_task() or ""
        if not task_id or not ledger.has(task_id):
            self._log(
                Text("uso: /undo-task [task_id] (no hay cambios registrados)", style="yellow"),
                plain="uso: /undo-task [task_id] (no hay cambios registrados)",
            )
            return
        resultado = ledger.restore(task_id)
        lines = [
            f"tarea {task_id}: restaurados {len(resultado['restored'])}, "
            f"eliminados {len(resultado['removed'])}"
        ]
        lines += [f"  ↩ {name}" for name in resultado["restored"]]
        lines += [f"  − {name} (era nuevo)" for name in resultado["removed"]]
        lines += [f"  · omitido: {name}" for name in resultado["skipped"]]
        self._log(Text("\n".join(lines)), plain=" ".join(lines))

    def _log_changes(self, task_id: str) -> None:
        """Al terminar, deja constancia de lo que la tarea cambió."""
        from satellite.persistence.changes import ChangeLedger

        ledger = ChangeLedger(self.session.project_root)
        if not task_id or not ledger.has(task_id):
            return
        files = ledger.files(task_id)
        self._log(
            render.changes_panel(
                task_id,
                files,
                ledger.diff(task_id),
                limit=render.CHANGES_TAIL_LINES,
                hint="/changes muestra el diff completo",
            ),
            plain=f"cambios de {task_id}: {', '.join(files)}",
        )

    def _cmd_stats(self, arg: str) -> None:
        report = self.session.stats()
        self._log(render.stats_table(report), plain=f"ahorro_total={report.total_savings}")
        self._log(Text(self.session.cache_report().format()), plain="cache")
        from satellite.context.compaction import format_usage

        usage = self.session.context_usage()
        self._log(Text(format_usage(usage)), plain=f"contexto {usage.percent}%")

    def _cmd_doctor(self, arg: str) -> None:
        checks = self.session.doctor()
        self._log(render.doctor_table(checks), plain="doctor")

    def _cmd_setup(self, arg: str) -> None:
        message = self.session.setup_sandbox()
        self._log(Text(message), plain="setup")

    def _cmd_config(self, arg: str) -> None:
        data = self.session.config_data()
        body = json.dumps(data, indent=2, ensure_ascii=False) if data else "(sin config)"
        self._log(Text(body), plain="config")

    def _cmd_provider(self, arg: str) -> None:
        """Abre el asistente de proveedor (catálogo + modelos + guardar)."""
        if arg.strip() == "list":
            self._log(render.providers_table(self.session), plain="provider-list")
            return

        def _done(message: str | None) -> None:
            if message:
                self._log(Text(message), plain="provider")
            self._refresh_status()

        self.push_screen(ProviderScreen(self.session), _done)

    def _cmd_mode(self, arg: str) -> None:
        mode = arg.strip().lower()
        if mode not in ("ask", "auto", "unrestricted"):
            self._log(Text("uso: /mode ask|auto|unrestricted", style="yellow"))
            return
        message = self.session.set_mode(mode)
        self._log(Text(message, style="green"), plain=message)
        self._refresh_status()

    def _cmd_safe_mode(self, arg: str) -> None:
        value = arg.strip().lower()
        enabled = value in ("on", "true", "1", "yes")
        message = self.session.set_safe_mode(enabled)
        self._log(Text(message, style="green"), plain=message)
        self._refresh_status()

    def _cmd_reset(self, arg: str) -> None:
        factory = "--factory" in arg
        message = self.session.reset(keep_skills=not factory)
        self._log(Text(message, style="green"), plain=message)
        self._refresh_status()

    def _cmd_restore(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /restore <nombre>", style="yellow"))
            return
        message = self.session.restore(arg.strip().split()[0])
        self._log(Text(message, style="green"), plain=message)

    def _cmd_clear(self, arg: str) -> None:
        self.action_clear_log()

    def _cmd_quit(self, arg: str) -> None:
        self.exit()

    # --- acciones de teclado ---

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()
        self.transcript.clear()

    def action_show_help(self) -> None:
        self._cmd_help("")

    def action_open_model(self) -> None:
        """Ctrl+P: abre el selector rápido de proveedor/modelo."""
        self._cmd_provider("")

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt", Input).focus()


def _stringify(renderable) -> str:
    if isinstance(renderable, str):
        return renderable
    plain = getattr(renderable, "plain", None)
    if isinstance(plain, str):
        return plain
    return type(renderable).__name__


def run_tui(project_root: Path | None = None) -> None:
    """Lanza la TUI interactiva."""
    SatelliteTUI(Session(project_root)).run()
