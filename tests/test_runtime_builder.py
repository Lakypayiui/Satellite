"""Tests del runtime_builder: 3 agentes base, catálogo global y cableado (Fase 6)."""

import json
from pathlib import Path

from satellite.cli.runtime_builder import build_runtime

BASE_IDS = {"orchestrator", "agent_creator", "skill_curator"}


def write_config(project: Path, payload: dict | None = None) -> None:
    cfg_dir = project / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(payload or {"name": "fake", "model": "fake-model"}),
        encoding="utf-8",
    )


def test_build_runtime_registers_base_agents(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    write_config(tmp_path)
    orchestrator, bus, store, registry = build_runtime(tmp_path)
    assert BASE_IDS <= {spec.id for spec in registry.all()}
    assert (tmp_path / "home" / "agents" / "orchestrator.json").exists()
    assert (tmp_path / "home" / "agents" / "orchestrator.system.txt").exists()
    assert (tmp_path / "home" / "agents" / "orchestrator.user.txt").exists()


def test_build_runtime_wires_services(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    write_config(tmp_path)
    orchestrator, bus, store, registry = build_runtime(tmp_path)
    assert getattr(orchestrator, "expander", None) is not None
    assert getattr(orchestrator, "skill_runner", None) is not None
    services = orchestrator.services
    assert services.approval is not None
    assert services.skills is not None
    assert services.sandbox is not None
    assert services.factory is not None
    assert services.context is not None


def test_provider_config_carries_max_tokens(tmp_path: Path) -> None:
    """El tope de salida configurado llega al provider (rol → proveedor → global)."""
    from satellite.cli.runtime_builder import _provider_config_from

    data = {
        "providers": [{"id": "p1", "name": "anthropic", "model": "claude-x", "max_tokens": 111}],
        "llm": {
            "max_tokens": {"creator": 512, "*": 128},
            "creator": {"provider": "p1", "model": "claude-x"},
            "agents": {"provider": "p1"},
        },
    }
    by_role = _provider_config_from(data, role="creator")
    assert by_role is not None and by_role.max_tokens == 512  # el rol manda

    wildcard = _provider_config_from(data, role="agents")
    assert wildcard is not None and wildcard.max_tokens == 128  # el "*" del usuario

    # sin comodín: manda el tope del proveedor guardado
    no_wildcard = {
        "providers": [{"id": "p1", "name": "anthropic", "model": "claude-x", "max_tokens": 111}],
        "llm": {"agents": {"provider": "p1"}},
    }
    fallback = _provider_config_from(no_wildcard, role="agents")
    assert fallback is not None and fallback.max_tokens == 111

    other_role = {
        "providers": [],
        "llm": {"agents": {"provider": "openai", "model": "gpt-4o-mini"}},
    }
    plain = _provider_config_from(other_role, role="agents")
    assert plain is not None and plain.max_tokens is None  # sin tope configurado

    scalar = {"llm": {"max_tokens": 256, "agents": {"provider": "openai", "model": "gpt-4o"}}}
    inherited = _provider_config_from(scalar, role="agents")
    assert inherited is not None and inherited.max_tokens == 256  # escalar global


def test_build_runtime_passes_custom_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    write_config(tmp_path)
    sentinel = object()
    orchestrator, _bus, _store, _registry = build_runtime(tmp_path, approval=sentinel)
    assert orchestrator.services.approval is sentinel


def test_tampered_base_spec_is_restored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    write_config(tmp_path)
    build_runtime(tmp_path)
    target = tmp_path / "home" / "agents" / "orchestrator.json"
    target.write_text('{"id": "orchestrator", "tampered": true}', encoding="utf-8")
    build_runtime(tmp_path)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data.get("tampered") is not True
    assert data["id"] == "orchestrator"
    assert "tampered" not in data


def test_corrupt_catalog_spec_is_quarantined(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    write_config(tmp_path)
    agents_dir = tmp_path / "home" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "broken.json").write_text("{not json", encoding="utf-8")
    orchestrator, _bus, _store, registry = build_runtime(tmp_path)
    assert (agents_dir / "_quarantine" / "broken.json").exists()
    assert BASE_IDS <= {spec.id for spec in registry.all()}


def test_user_agent_is_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    write_config(tmp_path)
    agents_dir = tmp_path / "home" / "agents"
    agents_dir.mkdir(parents=True)
    spec = {
        "id": "user_agent",
        "name": "User Agent",
        "description": "hace algo del usuario",
        "capabilities": ["do_user_thing"],
        "prompt_ref": "user_agent",
        "tools": [],
    }
    (agents_dir / "user_agent.json").write_text(json.dumps(spec), encoding="utf-8")
    _orch, _bus, _store, registry = build_runtime(tmp_path)
    assert registry.get("user_agent").id == "user_agent"
