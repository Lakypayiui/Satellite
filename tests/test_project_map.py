"""Índice del proyecto con lenguaje y líneas por archivo, y precontexto como mapa.

El JSON (esquema 2) guarda por archivo ``language`` y ``lines``, y en la cabecera los
totales, el reparto por lenguaje y las carpetas; el ruido del sistema no se indexa. El
precontexto ``map`` es el mapa sin contenido (más el README en preguntas generales).
"""

from __future__ import annotations

import json
from pathlib import Path

from satellite.context.project_index import ProjectIndex


def _project(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# Demo\n\nUna app de prueba.\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "util.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "main.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x01")
    (tmp_path / "pkg" / ".DS_Store").write_bytes(b"\x00\x01")
    (tmp_path / "demo.egg-info").mkdir()
    (tmp_path / "demo.egg-info" / "PKG-INFO").write_text("Name: demo\n", encoding="utf-8")
    return tmp_path


def test_the_index_stores_language_lines_totals_and_folders(tmp_path: Path) -> None:
    root = _project(tmp_path)
    ProjectIndex(root).ensure_persisted()

    data = json.loads((root / ".satellite" / "context" / "index.json").read_text(encoding="utf-8"))

    assert data["schema"] == 2
    by_path = {item["path"]: item for item in data["files"]}
    assert set(by_path) == {"README.md", "pkg/core.py", "pkg/util.py", "web/main.ts"}
    assert by_path["pkg/core.py"]["language"] == "python"
    assert by_path["pkg/core.py"]["lines"] == 2
    assert by_path["web/main.ts"]["language"] == "typescript"
    assert data["totals"] == {"files": 4, "lines": 3 + 2 + 1 + 1}
    assert [lang["id"] for lang in data["languages"]] == ["python", "typescript"]
    folders = {item["path"]: item for item in data["directories"]}
    assert folders["pkg"]["files"] == 2 and folders["pkg"]["language"] == "Python"


def test_an_old_schema_is_rebuilt_with_the_new_fields(tmp_path: Path) -> None:
    root = _project(tmp_path)
    index_path = root / ".satellite" / "context" / "index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps({"schema": 1, "root": str(root.resolve()), "files": []}), encoding="utf-8"
    )

    ProjectIndex(root).ensure_persisted()

    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["schema"] == 2 and data["totals"]["files"] == 4


def test_the_language_hint_comes_from_the_index(tmp_path: Path, monkeypatch) -> None:
    from satellite.context import languages

    def no_second_walk(*_args, **_kwargs):
        raise AssertionError("no debe recorrer el proyecto otra vez")

    monkeypatch.setattr(languages, "detect_project_languages", no_second_walk)
    hint = ProjectIndex(_project(tmp_path)).language_hint()

    assert hint.startswith("Python: 2 files")


def test_map_mode_has_no_file_contents_for_a_specific_question(tmp_path: Path) -> None:
    index = ProjectIndex(_project(tmp_path))

    text = index.context_for_goal("arregla la función a en core.py", mode="map")

    assert text.startswith("Project map")
    assert "pkg/ 2" in text and "Python" in text
    assert "--- " not in text  # ningún bloque de contenido
    assert len(text) <= 2_500


def test_map_mode_adds_the_readme_for_a_question_about_the_project(tmp_path: Path) -> None:
    index = ProjectIndex(_project(tmp_path))

    text = index.context_for_goal("¿de qué trata el proyecto?", mode="map")

    assert "--- README.md ---" in text and "Una app de prueba." in text
    assert "--- pkg/core.py ---" not in text


def test_full_mode_still_attaches_relevant_files(tmp_path: Path) -> None:
    index = ProjectIndex(_project(tmp_path))

    text = index.context_for_goal("revisa pkg/core.py", mode="full")

    assert text.startswith("Project map")
    assert "--- pkg/core.py ---" in text


def test_the_pilot_uses_the_configured_mode_and_the_env_override(tmp_path: Path, monkeypatch) -> None:
    from satellite.kernel.policy import load_settings
    from satellite.orchestration.pilot import PilotOrchestrator

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"context": {"precontext": "map"}}), encoding="utf-8")
    assert load_settings(cfg).precontext_mode == "map"
    cfg.write_text(json.dumps({"context": {"precontext": "raro"}}), encoding="utf-8")
    assert load_settings(cfg).precontext_mode == "map"  # el valor por defecto

    pilot = PilotOrchestrator(services=None, precontext_mode="map")  # type: ignore[arg-type]
    assert pilot._precontext_mode() == "map"
    monkeypatch.setenv("SATELLITE_PRECONTEXT", "full")
    assert pilot._precontext_mode() == "full"
