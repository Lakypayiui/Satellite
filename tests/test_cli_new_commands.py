"""Tests de los comandos nuevos de la CLI (Fase 8)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from satellite.cli.app import app


def _write_skill(skills_root: Path, skill_id: str, description: str) -> None:
    directory = skills_root / skill_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {"id": skill_id, "description": description, "entrypoint": "main.py"}
        ),
        encoding="utf-8",
    )
    (directory / "main.py").write_text("print(1)", encoding="utf-8")


def _use_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    return home


def test_skills_list(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    _write_skill(home / "skills", "count_words", "cuenta palabras de un texto")
    result = CliRunner().invoke(app, ["skills", "list"])
    assert result.exit_code == 0
    assert "count_words" in result.output


def test_skills_list_empty(tmp_path: Path, monkeypatch) -> None:
    _use_home(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["skills", "list"])
    assert result.exit_code == 0
    # sin skills, pero aclarando que las primitivas del runtime no son skills
    assert "no hay skills creadas" in result.output
    assert "satellite tools list" in result.output


def test_tools_list_shows_primitives(tmp_path: Path, monkeypatch) -> None:
    _use_home(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["tools", "list"])
    assert result.exit_code == 0
    for expected in ("run_python", "run_shell", "read_file", "write_file", "list_dir", "search_files"):
        assert expected in result.output
    assert "6 primitivas del kernel" in result.output


def test_skills_search(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    _write_skill(home / "skills", "count_words", "cuenta las palabras de un texto")
    result = CliRunner().invoke(app, ["skills", "search", "contar palabras"])
    assert result.exit_code == 0
    assert "count_words" in result.output


def test_skills_search_no_match(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    _write_skill(home / "skills", "count_words", "cuenta palabras")
    result = CliRunner().invoke(app, ["skills", "search", "zzqq unrelated"])
    assert result.exit_code == 0
    assert "(sin coincidencias)" in result.output


def test_skills_edit_and_delete(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    _write_skill(home / "skills", "count_words", "cuenta palabras")
    runner = CliRunner()
    edit = runner.invoke(
        app,
        ["skills", "edit", "count_words", "--description", "cuenta palabras de un texto largo"],
    )
    assert edit.exit_code == 0
    manifest = json.loads(
        (home / "skills" / "count_words" / "manifest.json").read_text(encoding="utf-8")
    )
    assert "largo" in manifest["description"]
    delete = runner.invoke(app, ["skills", "delete", "count_words"])
    assert delete.exit_code == 0
    assert not (home / "skills" / "count_words").exists()


def test_stats_command(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    _write_skill(home / "skills", "count_words", "cuenta palabras")
    result = CliRunner().invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "estimación" in result.output


def test_setup_command(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 0
    assert (home / "config" / "sandbox.json").exists()


def test_reset_command(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "user_agent.json").write_text("{}", encoding="utf-8")
    _write_skill(home / "skills", "count_words", "cuenta palabras")
    result = CliRunner().invoke(app, ["reset", "--keep-skills"])
    assert result.exit_code == 0
    assert not (home / "agents" / "user_agent.json").exists()
    assert (home / "skills" / "count_words").exists()


def test_reset_factory_removes_skills(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    _write_skill(home / "skills", "count_words", "cuenta palabras")
    result = CliRunner().invoke(app, ["reset", "--factory"])
    assert result.exit_code == 0
    assert not (home / "skills" / "count_words").exists()


def test_restore_command(tmp_path: Path, monkeypatch) -> None:
    _use_home(monkeypatch, tmp_path)
    from satellite.persistence.store import FileStore

    store = FileStore(tmp_path)
    store.migrate()
    snapshot = store.snapshot("pre")
    result = CliRunner().invoke(app, ["restore", snapshot.name, "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert "restaurado" in result.output


def test_safe_mode_filters_user_agents(tmp_path: Path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path)
    cfg_dir = tmp_path / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "fake-model"}), encoding="utf-8"
    )
    (home / "agents").mkdir(parents=True, exist_ok=True)
    (home / "agents" / "user_agent.json").write_text(
        json.dumps(
            {
                "id": "user_agent",
                "name": "User",
                "description": "d",
                "capabilities": ["c"],
                "prompt_ref": "user_agent",
            }
        ),
        encoding="utf-8",
    )

    from satellite.cli.runtime_builder import build_runtime, set_safe_mode

    set_safe_mode(True)
    try:
        _orch, _bus, _store, registry = build_runtime(tmp_path)
    finally:
        set_safe_mode(False)
    ids = {spec.id for spec in registry.all()}
    assert "user_agent" not in ids
    assert {"orchestrator", "agent_creator", "skill_curator"} <= ids
