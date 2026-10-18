"""Comentarios inline y cola: persistencia compartida y descubrimiento en las tres interfaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from satellite import comments, prompt_queue
from satellite import commands as catalogo
from satellite.tui import palette


def test_comments_roundtrip_validation_and_persistence(tmp_path: Path) -> None:
    item = comments.add_comment(
        tmp_path,
        file="src/app.py",
        line_start=3,
        line_end=5,
        text="revisar esto",
        session_id="s1",
        task_id="t1",
    )
    assert item["resolved"] is False
    assert item["file"] == "src/app.py"
    assert comments.list_comments(tmp_path, task_id="t1")[0]["id"] == item["id"]
    assert len(comments.list_comments(tmp_path, include_resolved=False)) == 1

    comments.update_comment(tmp_path, item["id"], resolved=True, text="hecho")
    assert comments.list_comments(tmp_path)[0]["resolved"] is True
    assert comments.list_comments(tmp_path, include_resolved=False) == []

    assert comments.delete_comment(tmp_path, item["id"]) is True
    assert comments.list_comments(tmp_path) == []
    assert comments.delete_comment(tmp_path, "no-existe") is False

    with pytest.raises(comments.CommentError):
        comments.add_comment(tmp_path, file="../fuera", line_start=1, text="x")
    with pytest.raises(comments.CommentError):
        comments.add_comment(tmp_path, file="a", line_start=0, text="x")
    with pytest.raises(comments.CommentError):
        comments.add_comment(tmp_path, file="a", line_start=1, text="   ")


def test_queue_is_fifo_persisted_and_editable(tmp_path: Path) -> None:
    prompt_queue.enqueue(tmp_path, "uno")
    prompt_queue.enqueue(tmp_path, "dos", plan_mode=True)
    assert [item["text"] for item in prompt_queue.list_items(tmp_path)] == ["uno", "dos"]

    primero = prompt_queue.pop_next(tmp_path)
    assert primero is not None and primero["text"] == "uno"
    assert [item["text"] for item in prompt_queue.list_items(tmp_path)] == ["dos"]

    item_id = prompt_queue.list_items(tmp_path)[0]["id"]
    prompt_queue.update_item(tmp_path, item_id, text="dos (editado)")
    assert prompt_queue.list_items(tmp_path)[0]["text"] == "dos (editado)"

    prompt_queue.remove_item(tmp_path, item_id)
    assert prompt_queue.list_items(tmp_path) == []
    assert prompt_queue.pop_next(tmp_path) is None

    with pytest.raises(prompt_queue.QueueError):
        prompt_queue.enqueue(tmp_path, "   ")
    with pytest.raises(KeyError):
        prompt_queue.remove_item(tmp_path, "no-existe")


def test_new_commands_are_discoverable_in_help_palette_and_catalog() -> None:
    pytest.importorskip("textual")
    from satellite.tui.app import SatelliteTUI

    for command_id in ("git", "worktrees", "comments", "queue"):
        spec = catalogo.get(command_id)
        assert spec is not None
        # Las tres superficies tienen la capacidad (el escritorio ve la cola en el compositor).
        assert {"tui", "cli", "desktop"} <= set(spec.surfaces)
        # La ayuda de la TUI exige que el usage empiece por «/<comando>».
        assert spec.usage.startswith(f"/{command_id}")
        assert command_id in palette.command_names()
        assert f"/{command_id}" in palette.help_text()

    for handler in ("_cmd_git", "_cmd_worktrees", "_cmd_comments", "_cmd_queue"):
        assert callable(getattr(SatelliteTUI, handler, None))
