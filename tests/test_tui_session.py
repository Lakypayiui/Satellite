"""Tests de la capa de sesión de la TUI (sin terminal)."""

import asyncio
import json
from pathlib import Path

from satellite.tui.session import Session


def run(coro):
    return asyncio.run(coro)


def make_session(tmp_path: Path, monkeypatch, *, config: dict | None = None) -> Session:
    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    if config is not None:
        cfg_dir = tmp_path / ".satellite" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return Session(tmp_path, home=home)


def write_skill(home: Path, skill_id: str, description: str) -> None:
    directory = home / "skills" / skill_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"id": skill_id, "description": description, "entrypoint": "main.py"}),
        encoding="utf-8",
    )
    (directory / "main.py").write_text("print(1)", encoding="utf-8")


def test_status_without_provider(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    status = session.status()
    assert status.agents >= 3
    assert status.skills == 0
    assert status.provider == ""
    assert status.policy_mode == "ask"
    assert "pilot" in {spec.id for spec in session.agents()}


def test_agents_includes_base_catalog(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    ids = {spec.id for spec in session.agents()}
    assert {"pilot", "agent_creator", "skill_curator"} <= ids


def test_skills_and_search(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    write_skill(tmp_path / "home", "count_words", "cuenta las palabras de un texto")
    assert [manifest.id for manifest in session.skills()] == ["count_words"]
    hits = session.skill_search("contar palabras")
    assert hits and hits[0].id == "count_words"


def test_skill_edit_and_delete(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    write_skill(tmp_path / "home", "count_words", "cuenta palabras")
    manifest = session.skill_edit("count_words", description="cuenta palabras de un texto", tags=["texto"])
    assert "texto" in manifest.description
    assert manifest.tags == ("texto",)
    session.skill_delete("count_words")
    assert session.skills() == []


def test_stats_report(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    write_skill(tmp_path / "home", "count_words", "cuenta palabras")
    report = session.stats()
    assert report.baseline_is_estimate is True
    assert len(report.skills) == 1


def test_doctor_reports_checks(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch, config={"name": "fake", "model": "m"})
    checks = session.doctor()
    assert checks
    labels = " | ".join(message for _ok, message in checks)
    assert "sandbox" in labels
    assert "esquema" in labels


def test_set_mode_writes_config(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    session.set_mode("auto")
    data = json.loads((tmp_path / ".satellite" / "config" / "config.json").read_text(encoding="utf-8"))
    assert data["policy_mode"] == "auto"
    assert session.status().policy_mode == "auto"


def test_safe_mode_filters_user_agents(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch, config={"name": "fake", "model": "m"})
    agents_dir = tmp_path / "home" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "user_agent.json").write_text(
        json.dumps(
            {
                "id": "user_agent",
                "name": "U",
                "description": "d",
                "capabilities": ["c"],
                "prompt_ref": "user_agent",
            }
        ),
        encoding="utf-8",
    )
    assert "user_agent" in {spec.id for spec in session.agents()}
    session.set_safe_mode(True)
    try:
        _orch, _bus, _store, registry = session.runtime(force=True)
        assert "user_agent" not in {spec.id for spec in registry.all()}
    finally:
        session.set_safe_mode(False)


def test_run_goal_chat_direct(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch, config={"name": "fake", "model": "m"})
    result = run(session.run_goal("¿qué es la fotosíntesis?"))
    assert result.status.value == "ok"
    assert result.output.get("mode") == "pilot_reply"


def test_create_agent_returns_outcome(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch, config={"name": "fake", "model": "m"})
    outcome = run(session.create_agent("resumir documentos largos"))
    assert outcome.kind in ("error", "reuse", "needs_clarification", "created", "rejected")


def test_send_message_is_conversational(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch, config={"name": "fake", "model": "m"})
    reply = run(session.send_message("¿qué es la fotosíntesis?"))
    assert reply.status == "ok"
    assert session.conversation()[0].role == "user"
    assert [turn.role for turn in session.conversation()] == ["user", "assistant"]
    session.new_conversation()
    assert session.conversation() == ()


def test_send_message_survives_planner_error(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch, config={"name": "fake", "model": "m"})
    reply = run(session.send_message("necesito agregar login"))
    assert reply.status in ("ok", "failed", "needs_user_input")
    # pase lo que pase la conversación sigue viva
    assert len(session.conversation()) == 2


def test_curate_returns_outcome(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch, config={"name": "fake", "model": "m"})
    write_skill(tmp_path / "home", "count_words", "cuenta palabras")
    outcome = run(session.curate("revisa"))
    assert outcome.kind in ("error", "empty", "applied", "proposed")


def test_tasks_and_snapshots(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    assert session.tasks() == []
    assert session.snapshots() == []
    from satellite.persistence.store import FileStore

    store = FileStore(tmp_path)
    store.migrate()
    snapshot = store.snapshot("pre")
    assert snapshot.name in session.snapshots()


def test_reset_keeps_skills_by_default(tmp_path, monkeypatch) -> None:
    session = make_session(tmp_path, monkeypatch)
    write_skill(tmp_path / "home", "count_words", "cuenta palabras")
    agents_dir = tmp_path / "home" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "x.json").write_text("{}", encoding="utf-8")
    session.reset()
    assert not (agents_dir / "x.json").exists()
    assert (tmp_path / "home" / "skills" / "count_words").exists()
    session.reset(keep_skills=False)
    assert not (tmp_path / "home" / "skills" / "count_words").exists()
