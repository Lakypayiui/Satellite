"""TUI interactiva de Satellite (Textual), tipo opencode/hermes.

Toda la lógica vive en ``session.py``/``palette.py``; aquí sólo está la capa de
presentación e interacción.
"""

from __future__ import annotations

import json
import shlex
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

PROMPT_HINT = "Escríbele al agente: «necesito agregar X», «revisa esto»…  ·  /help"


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

    CSS = """
    Screen { background: $surface; }
    #main { height: 1fr; }
    #log { height: 1fr; width: 1fr; border: round $accent; padding: 0 1; }
    #agents-panel {
        width: 34; height: 1fr; display: none;
        border: round $accent; background: $surface; padding: 0 1;
    }
    .agent-card {
        border: round $secondary; background: $panel;
        padding: 0 1; margin-bottom: 1; height: auto;
    }
    .agent-card.running { border: round $accent; }
    .agent-card.done { border: round $success; }
    .agent-card.failed { border: round $error; }
    #statusbar { height: 1; padding: 0 1; }
    #prompt { border: round $accent; }
    #approval-box { width: 78; height: auto; border: thick $warning; background: $panel; padding: 1 2; }
    #approval-actions { height: auto; align-horizontal: center; }
    #approval-actions Button { margin: 0 2; }
    #plan-box {
        width: 84; max-width: 95%; height: auto; max-height: 90%;
        border: thick $warning; background: $panel; padding: 1 2;
    }
    #plan-actions { height: auto; }
    #plan-actions Button { width: 1fr; margin-bottom: 1; }
    #approval-scroll { height: auto; max-height: 24; }
    #provider-box {
        width: 92; height: auto; max-height: 90%;
        border: thick $accent; background: $panel; padding: 1 2;
    }
    #provider-box Input { margin-bottom: 1; }
    #provider-apps { height: 10; }
    #provider-models { height: 8; }
    #provider-actions { height: auto; align-horizontal: center; }
    #provider-actions Button { margin: 0 2; }
    #provider-status { height: auto; color: $text-muted; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Salir"),
        ("ctrl+l", "clear_log", "Limpiar"),
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
                    self._upsert_agent(
                        self._agent_of(event), status="running", action=f"⚙ {tool}"
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
        except Exception:  # noqa: BLE001 — el panel nunca debe tumbar la sesión
            return

    # --- entrada ---

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        name, arg = palette.parse(line)
        if not name:
            return
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
        self._busy(True, "el agente está trabajando…")
        try:
            reply = await self.session.send_message(
                text,
                approval=self._approval_for_session(),
                plan_mode=plan_mode,
                extra_metadata=extra_metadata,
            )
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
            self._log(render.reply_panel(reply), plain=f"respuesta {reply.status}")
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
        try:
            reply = await self.session.choose_option(option, approval=self.approval)
            self._log(render.reply_panel(reply), plain=f"respuesta {reply.status}")
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

    def _cmd_run(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /run <mensaje>", style="yellow"))
            return
        self._spawn(self._do_chat(arg.strip()), name="chat")

    def _cmd_plan(self, arg: str) -> None:
        if not arg.strip():
            self._log(Text("uso: /plan <tarea ambigua o a planificar>", style="yellow"))
            return
        self._spawn(self._do_chat(arg.strip(), plan_mode=True), name="plan")

    def _cmd_new(self, arg: str) -> None:
        self.session.new_conversation()
        self._log(Text("conversación nueva", style="green"), plain="new")

    def _cmd_history(self, arg: str) -> None:
        turns = self.session.conversation()
        self._log(render.conversation_table(turns), plain=f"history={len(turns)}")

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
