"""Tests de invariantes del kernel: manifiesto, ids reservados y settings (Fase 1)."""

from pathlib import Path

from satellite.core.events import EventType
from satellite.kernel.manifest import (
    RESERVED_AGENT_IDS,
    RESERVED_SKILL_IDS,
    SPAWNABLE_RESERVED_AGENT_IDS,
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

    La fuente central es **agregada**: los ids del kernel (`RESERVED_SKILL_IDS`) más
    los del manifiesto de tools (`satellite/tool_manifest.py`), que existe para no
    meter ids nuevos en el kernel. Los ids nuevos NO tienen que vivir en el kernel.
    """
    from satellite.cli.runtime_builder import build_tool_registry
    from satellite.tool_manifest import TOOL_IDS, reserved_ids

    tool_ids = {tool.id for tool in build_tool_registry().all()}
    assert tool_ids, "el registro de tools no debería estar vacío"
    agregado = reserved_ids()
    assert tool_ids <= agregado, sorted(tool_ids - agregado)
    # El manifiesto de tools aporta ids que el kernel no conoce.
    assert TOOL_IDS <= agregado
    assert TOOL_IDS - RESERVED_SKILL_IDS


def test_tool_manifest_ids_live_outside_kernel() -> None:
    """Los ids de tools no-kernel viven fuera del kernel y son reservados ahí."""
    from satellite.tool_manifest import TOOL_IDS, is_reserved_id, is_tool_id

    assert TOOL_IDS == {"desktop_list_windows", "desktop_observe", "desktop_act"}
    assert not (TOOL_IDS & RESERVED_SKILL_IDS)
    assert is_tool_id("desktop_observe")
    assert is_reserved_id("desktop_observe")
    assert is_reserved_id("read_file")  # sigue cubriendo el kernel
    assert not is_reserved_id("docs_writer")


def test_desktop_tools_registered_only_when_allowed() -> None:
    """Gate de superficies: default/MCP no registra; CLI/TUI sí (tras los gates)."""
    from satellite.cli.runtime_builder import build_tool_registry
    from satellite.tool_manifest import TOOL_IDS, reserved_ids

    default_ids = {tool.id for tool in build_tool_registry().all()}
    assert default_ids.isdisjoint(TOOL_IDS)
    runtime_ids = {tool.id for tool in build_tool_registry(allow_desktop=True).all()}
    assert TOOL_IDS <= runtime_ids
    assert runtime_ids <= reserved_ids()


def test_reserved_ids() -> None:
    language_agents = {
        f"{language}_{role}"
        for language in (
            "python",
            "javascript",
            "typescript",
            "java",
            "csharp",
            "cpp",
            "sql",
            "go",
            "php",
            "rust",
        )
        for role in ("coder", "auditor")
    }
    assert RESERVED_AGENT_IDS == {
        "orchestrator",
        "agent_creator",
        "skill_curator",
        "pele_diagram_agent",
        "desktop_operator",
        "web_researcher",
    } | language_agents
    assert "run_python" in RESERVED_SKILL_IDS
    assert "web_search" in RESERVED_SKILL_IDS
    assert "web_fetch" in RESERVED_SKILL_IDS
    assert "web_download" in RESERVED_SKILL_IDS
    assert "lsp" in RESERVED_SKILL_IDS
    assert "code_task" in RESERVED_SKILL_IDS
    assert is_reserved("orchestrator")
    assert is_reserved("pele_diagram_agent")
    assert is_reserved("search_files")
    assert not is_reserved("docs_writer")


def test_spawnable_reserved_agents_are_only_language_coders() -> None:
    assert len(SPAWNABLE_RESERVED_AGENT_IDS) == 10
    assert SPAWNABLE_RESERVED_AGENT_IDS <= RESERVED_AGENT_IDS
    assert "python_coder" in SPAWNABLE_RESERVED_AGENT_IDS
    assert "rust_coder" in SPAWNABLE_RESERVED_AGENT_IDS
    assert "python_auditor" not in SPAWNABLE_RESERVED_AGENT_IDS
    assert "orchestrator" not in SPAWNABLE_RESERVED_AGENT_IDS


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
