"""Internal context scaffolding stays in English.

The project content may be in any language; that must not make the model infer
the answer language. These labels used to be Spanish and leaked into the
visible answer.
"""

from pathlib import Path

from satellite.context.project_index import ProjectIndex
from satellite.context.turn_summaries import render_turn_summaries


def test_project_context_header_is_english(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Proyecto\n\nEsto es una prueba en español.\n", encoding="utf-8"
    )
    index = ProjectIndex(tmp_path)
    index.ensure_persisted()
    context = index.context_for_goal("what is this project")
    assert context.startswith("Project structure (tree up to two levels):")


def test_session_memory_labels_are_english() -> None:
    rendered = render_turn_summaries(
        [{"user_summary": "Asked for a summary.", "assistant_summary": "Summarized."}]
    )
    assert "- Turn 1. User:" in rendered
    assert "Satellite:" in rendered
    assert "Turno" not in rendered
    assert "Usuario" not in rendered
