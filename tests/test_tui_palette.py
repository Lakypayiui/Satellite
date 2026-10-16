"""Tests de la paleta de comandos de la TUI (sin UI)."""

from satellite.tui import palette


def test_parse_free_text_is_a_goal() -> None:
    assert palette.parse("crea una landing page") == ("run", "crea una landing page")


def test_parse_slash_command() -> None:
    assert palette.parse("/help") == ("help", "")
    assert palette.parse("/agents") == ("agents", "")
    assert palette.parse("/skill-edit foo --tags a,b") == ("skill-edit", "foo --tags a,b")


def test_parse_q_alias_and_empty() -> None:
    assert palette.parse("/q") == ("quit", "")
    assert palette.parse("   ") == ("", "")


def test_command_lookup() -> None:
    assert palette.get("stats") is not None
    assert palette.get("skill-search") is not None
    assert palette.get("no-existe") is None


def test_all_commands_listed_in_help() -> None:
    text = palette.help_text()
    assert "Comandos disponibles" in text
    for name in palette.command_names():
        assert f"/{name}" in text


def test_help_covers_the_whole_feature_set() -> None:
    names = set(palette.command_names())
    expected = {
        "run", "agents", "create", "skills", "skill-search", "skill-edit",
        "skill-delete", "curate", "tasks", "status", "inspect", "stats",
        "doctor", "setup", "config", "mode", "safe-mode", "reset", "restore",
        "snapshots", "clear", "quit", "help", "changes", "undo-task",
        "sessions", "session", "cancel",
    }
    assert expected <= names
