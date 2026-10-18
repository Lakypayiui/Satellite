"""Render de la TUI: tarjetas semánticas, respuesta markdown y barra superior."""

from types import SimpleNamespace

from rich.markdown import Markdown

from satellite.core.events import EventType, ExecutionEvent
from satellite.tui import palette, render


# --- acciones semánticas ---


def test_tool_action_is_semantic_and_names_the_target() -> None:
    assert render.tool_action("read_file", {"path": "docs/README.md"}) == "Leyendo README.md"
    assert render.tool_action("edit_file", {"path": "a/api.py"}) == "Editando api.py"
    assert render.tool_action("write_file", {"path": "x/y.py"}) == "Escribiendo y.py"
    assert render.tool_action("run_command", {"command": "pytest -q"}) == "Ejecutando pytest"
    assert render.tool_action("run_command", {}) == "Ejecutando un comando"
    assert render.tool_action("search_files", {"pattern": "**/*.py"}) == "Buscando **/*.py"


def test_action_card_read_shows_path_and_line_count() -> None:
    card = render.action_card("read_file", {"path": "a.py"}, output="uno\ndos\ntres")
    assert "📖 Read a.py" in str(card.title)
    assert "3 líneas" in card.renderable.plain


def test_action_card_edit_renders_colored_diff() -> None:
    card = render.action_card(
        "edit_file",
        {"path": "a.py", "old_string": "viejo", "new_string": "nuevo"},
    )
    assert "📝 Update a.py" in str(card.title)
    body = card.renderable
    assert "-viejo" in body.plain
    assert "+nuevo" in body.plain
    estilos = {str(span.style) for span in body.spans}
    assert render.SUCCESS in estilos
    assert render.DANGER in estilos


def test_action_card_terminal_shows_command_and_output() -> None:
    card = render.action_card(
        "run_command",
        {"command": "pytest -q"},
        output={"exit_code": 0, "stdout": "ok\n", "stderr": ""},
    )
    assert "❯ Terminal pytest -q" in str(card.title)
    assert "exit 0" in card.renderable.plain
    assert "ok" in card.renderable.plain


def test_action_card_search_reports_the_count() -> None:
    card = render.action_card(
        "search_files", {"pattern": "**/*.py"}, output=[{"path": "a.py"}, {"path": "b.py"}]
    )
    assert "🔍 Search **/*.py" in str(card.title)
    assert "2 resultado(s)" in card.renderable.plain


def test_event_text_uses_a_card_for_tool_called() -> None:
    event = ExecutionEvent(
        event_type=EventType.TOOL_CALLED,
        task_id="t1",
        tool_name="read_file",
        payload={"arguments": {"path": "a.py"}, "success": True, "output": "x\ny"},
    )
    from rich.panel import Panel

    assert isinstance(render.event_text(event), Panel)
    otro = ExecutionEvent(event_type=EventType.NODE_STARTED, task_id="t1", node_id="n1")
    assert not isinstance(render.event_text(otro), Panel)


# --- respuesta y tarjeta de usuario ---


def test_reply_panel_uses_markdown_and_footer_metrics() -> None:
    reply = SimpleNamespace(
        text="# Hola\n\n```python\nprint(1)\n```",
        status="ok",
        error=None,
        task_id="t1",
        metrics={"tokens_in": 10, "tokens_out": 5},
    )
    panel = render.reply_panel(reply, elapsed_ms=1500)
    assert str(panel.title) == "🤖 Satellite"
    cuerpo = list(panel.renderable.renderables)
    assert isinstance(cuerpo[0], Markdown)
    footer = cuerpo[-1]
    assert "tokens 10/5" in footer.plain
    assert "1.5 s" in footer.plain
    assert "t1" in footer.plain


def test_user_card_badge() -> None:
    panel = render.user_card("necesito agregar login")
    assert str(panel.title) == "👤 Tú"
    assert "necesito agregar login" in panel.renderable.plain


# --- barra superior ---


def test_status_bar_shows_pills_without_gears(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    status = SimpleNamespace(
        project_root=str(tmp_path),
        provider="openai",
        model="gpt-x",
        sandbox="N0",
        policy_mode="ask",
        safe_mode=False,
        agents=3,
        skills=1,
        context_percent=10,
    )
    bar = render.status_bar(status)
    assert "⚙" not in bar.plain
    assert "openai/gpt-x" in bar.plain
    assert "main" in bar.plain
    assert "sandbox N0" in bar.plain
    assert "ask" in bar.plain


def test_git_branch_reads_head(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n", encoding="utf-8")
    assert render.git_branch(tmp_path) == "feature/x"
    assert render.git_branch(tmp_path / "no-existe") == ""


# --- alias /model ---


def test_model_alias_opens_the_provider_selector() -> None:
    assert palette.parse("/model") == ("provider", "")
    assert palette.parse("/models") == ("provider", "")
    assert "/model" in palette.help_text()
