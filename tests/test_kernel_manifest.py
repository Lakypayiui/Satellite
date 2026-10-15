"""Tests de invariantes del kernel: manifiesto, ids reservados y settings (Fase 1)."""

from pathlib import Path

from satellite.core.events import EventType
from satellite.kernel.manifest import (
    RESERVED_AGENT_IDS,
    RESERVED_SKILL_IDS,
    is_reserved,
    sha256_file,
    verify_and_restore,
)
from satellite.kernel.policy import PolicyMode, Quotas, load_settings


def test_event_types_extended() -> None:
    expected = {
        "agent_proposed",
        "agent_approved",
        "agent_rejected",
        "agent_created",
        "skill_proposed",
        "skill_approved",
        "skill_executed",
        "skill_failed",
        "skill_quarantined",
        "delegation_started",
        "delegation_completed",
        "policy_violation",
        "safe_mode_entered",
        "plan_failed",
    }
    present = {e.value for e in EventType}
    assert expected <= present


def test_reserved_ids_cover_every_runtime_tool() -> None:
    """Guardia: si el runtime suma una tool, tiene que estar reservada.

    Evita que un agente o una skill puedan suplantar un id del kernel (hoy
    faltaban siete: `edit_file`, `run_command`, `git_status`, `request_context`,
    `delegate`, `search_skills` y `propose_skill`).
    """
    from satellite.cli.runtime_builder import build_tool_registry

    tool_ids = {tool.id for tool in build_tool_registry().all()}
    assert tool_ids, "el registro de tools no debería estar vacío"
    assert tool_ids <= RESERVED_SKILL_IDS, sorted(tool_ids - RESERVED_SKILL_IDS)


def test_reserved_ids() -> None:
    assert RESERVED_AGENT_IDS == {"orchestrator", "agent_creator", "skill_curator"}
    assert "run_python" in RESERVED_SKILL_IDS
    assert is_reserved("orchestrator")
    assert is_reserved("search_files")
    assert not is_reserved("docs_writer")


def test_verify_and_restore(tmp_path: Path) -> None:
    source = tmp_path / "pkg"
    data = tmp_path / "data"
    source.mkdir()
    (source / "orchestrator.json").write_text('{"id": "orchestrator"}', encoding="utf-8")
    (source / "orchestrator.txt").write_text("prompt", encoding="utf-8")

    # primera sync: todo restaurado
    restored = verify_and_restore(source, data)
    assert sorted(restored) == ["orchestrator.json", "orchestrator.txt"]
    assert sha256_file(data / "orchestrator.json") == sha256_file(source / "orchestrator.json")

    # sin cambios: nada restaurado
    assert verify_and_restore(source, data) == []

    # divergencia: restaurar desde el paquete
    (data / "orchestrator.json").write_text('{"id": "tampered"}', encoding="utf-8")
    assert verify_and_restore(source, data) == ["orchestrator.json"]
    assert sha256_file(data / "orchestrator.json") == sha256_file(source / "orchestrator.json")


def test_load_settings_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "nope.json")
    assert settings.policy_mode is PolicyMode.ASK
    assert settings.quotas.max_agents == 50
    assert settings.sandbox_level == "N0"


def test_load_settings_reads_values(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(
        '{"policy_mode": "auto", "quotas": {"max_agents": 5}, "refine_goal": true}',
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.policy_mode is PolicyMode.AUTO
    assert settings.quotas.max_agents == 5
    assert settings.quotas.max_skills == Quotas().max_skills
    assert settings.refine_goal is True


def test_load_settings_tolerant_to_corrupt(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json", encoding="utf-8")
    assert load_settings(cfg).policy_mode is PolicyMode.ASK


def test_quotas_roundtrip() -> None:
    quotas = Quotas(max_agents=7, proposals_per_task=3)
    assert Quotas.from_dict(quotas.to_dict()) == quotas
