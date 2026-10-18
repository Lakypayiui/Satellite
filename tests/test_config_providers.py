"""Reglas de guardado/activación de proveedores (módulo compartido)."""

import json
from pathlib import Path

import pytest

from satellite.config import providers as pconfig


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read(root: Path) -> dict:
    return json.loads((root / pconfig.CONFIG_PATH).read_text(encoding="utf-8"))


def read_global() -> dict:
    return json.loads(pconfig.global_config_path().read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolate_global_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "global"))


def test_load_config_missing_and_invalid(tmp_path: Path) -> None:
    assert pconfig.load_config(tmp_path) == {}
    path = tmp_path / pconfig.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{no soy json", encoding="utf-8")
    assert pconfig.load_config(tmp_path) == {}


def test_save_config_is_atomic_and_valid(tmp_path: Path) -> None:
    pconfig.save_config(tmp_path, {"a": 1})
    assert json.loads((tmp_path / pconfig.CONFIG_PATH).read_text(encoding="utf-8")) == {"a": 1}
    assert not list((tmp_path / ".satellite" / "config").glob("*.tmp"))


def test_save_provider_assigns_unique_id_and_sets_global(tmp_path: Path) -> None:
    saved = pconfig.save_provider(
        tmp_path, name="openai", model="gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-1"
    )
    assert saved.id and saved.api_key_set is True
    data = read(tmp_path)
    assert data["llm"]["provider"] == "openai"
    assert data["llm"]["model"] == "gpt-4o"
    assert data["llm"]["provider_id"] == saved.id
    assert "api_key" not in data["llm"]
    assert read_global()["providers"][0]["api_key"] == "sk-1"


def test_save_provider_reuses_id_only_for_exact_match(tmp_path: Path) -> None:
    first = pconfig.save_provider(
        tmp_path, name="openai", model="gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-1"
    )
    again = pconfig.save_provider(
        tmp_path, name="openai", model="gpt-4o", base_url="https://api.openai.com/v1"
    )
    assert again.id == first.id
    assert len(pconfig.list_providers(tmp_path)) == 1
    # mismo servicio, otro modelo: id nuevo y conviven
    other = pconfig.save_provider(
        tmp_path, name="openai", model="gpt-4o-mini", base_url="https://api.openai.com/v1"
    )
    assert other.id != first.id
    assert len(pconfig.list_providers(tmp_path)) == 2
    # y hereda la api_key del mismo servicio (name+base_url)
    entries = read_global()["providers"]
    assert entries[-1]["api_key"] == "sk-1"


def test_api_key_is_never_exposed(tmp_path: Path) -> None:
    pconfig.save_provider(
        tmp_path, name="groq", model="llama-3", base_url="https://api.groq.com/openai/v1", api_key="gsk-secret"
    )
    for provider in pconfig.list_providers(tmp_path):
        assert "gsk-secret" not in json.dumps(provider.to_dict())
        assert provider.api_key_set is True
    assert "gsk-secret" not in json.dumps(pconfig.active_public(tmp_path))


def test_project_credentials_are_ignored_and_global_credentials_are_used(tmp_path: Path) -> None:
    saved = pconfig.save_provider(
        tmp_path,
        name="openai",
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="global-only-test-key",
    )
    write(
        tmp_path / pconfig.CONFIG_PATH,
        {
            "providers": [{
                "id": saved.id,
                "name": saved.name,
                "model": saved.model,
                "base_url": saved.base_url,
                "api_key": "project-must-not-be-used",
            }],
            "llm": {
                "provider_id": saved.id,
                "orchestrator": {"provider": saved.id, "api_key": "role-must-not-be-used"},
            },
        },
    )

    runtime = pconfig.runtime_config(tmp_path)
    connection = pconfig.active_connection(tmp_path)

    assert runtime["providers"][0]["api_key"] == "global-only-test-key"
    assert connection["api_key"] == "global-only-test-key"
    assert runtime["llm"]["orchestrator"]["provider"] == saved.id
    assert "api_key" not in runtime["llm"]["orchestrator"]


def test_api_key_set_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MI_CLAVE", "x")
    pconfig.save_provider(tmp_path, name="openai", model="m", base_url="u", api_key_env="MI_CLAVE")
    provider = pconfig.list_providers(tmp_path)[0]
    assert provider.api_key_set is True
    assert provider.api_key_env == "MI_CLAVE"


def test_active_provider_resolves_by_id_then_name(tmp_path: Path) -> None:
    saved = pconfig.save_provider(tmp_path, name="openai", model="gpt-4o", base_url="u", api_key="k")
    assert pconfig.active_provider(tmp_path) is not None
    assert pconfig.active_provider(tmp_path).id == saved.id
    assert pconfig.active_id(tmp_path) == saved.id


def test_activate_writes_global_for_runtime(tmp_path: Path) -> None:
    saved = pconfig.save_provider(tmp_path, name="groq", model="llama-3", base_url="https://api.groq.com/openai/v1", api_key="k")
    report = pconfig.activate(tmp_path, saved.id)
    data = read(tmp_path)
    # El proyecto guarda referencias; proveedor y clave quedan globales.
    assert data["name"] == "groq"
    assert data["model"] == "llama-3"
    assert data["base_url"] == "https://api.groq.com/openai/v1"
    assert "api_key" not in data
    assert read_global()["providers"][0]["api_key"] == "k"
    assert report.provider.id == saved.id


def test_activate_follows_roles_pointing_to_previous_global(tmp_path: Path) -> None:
    first = pconfig.save_provider(tmp_path, name="openai", model="a", base_url="https://api.openai.com/v1", api_key="k")
    pconfig.set_roles(tmp_path, {"orchestrator": {"provider": first.id, "model": "a"}})
    second = pconfig.save_provider(tmp_path, name="groq", model="b", base_url="https://api.groq.com/openai/v1", api_key="k")

    report = pconfig.activate(tmp_path, second.id, previous_id=first.id)
    assert "orchestrator" in report.followed_roles
    role = read(tmp_path)["llm"]["orchestrator"]
    assert role["provider"] == second.id
    assert role["model"] == "b"


def test_activate_respects_pinned_roles(tmp_path: Path) -> None:
    a = pconfig.save_provider(tmp_path, name="openai", model="a", base_url="https://api.openai.com/v1", api_key="k")
    b = pconfig.save_provider(tmp_path, name="groq", model="b", base_url="https://api.groq.com/openai/v1", api_key="k")
    pconfig.set_roles(tmp_path, {"creator": {"provider": a.id, "model": "a"}})

    report = pconfig.activate(tmp_path, b.id)
    assert "creator" in report.pinned_roles
    assert read(tmp_path)["llm"]["creator"]["provider"] == a.id


def test_activate_unknown_provider_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        pconfig.activate(tmp_path, "noexiste")


def test_set_roles_and_clear_roles(tmp_path: Path) -> None:
    pconfig.set_roles(tmp_path, {"agents": {"provider": "x", "model": "m"}})
    assert read(tmp_path)["llm"]["agents"]["model"] == "m"
    # vacío = borrar ese rol
    pconfig.set_roles(tmp_path, {"agents": {}})
    assert "agents" not in read(tmp_path)["llm"]
    pconfig.set_roles(tmp_path, {"context": {"provider": "y", "model": "m2"}})
    pconfig.clear_roles(tmp_path)
    assert pconfig.role_config(tmp_path) == {}


def test_set_roles_rejects_unknown_role(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        pconfig.set_roles(tmp_path, {"inventado": {"provider": "x"}})


def test_delete_provider_by_id_and_name(tmp_path: Path) -> None:
    saved = pconfig.save_provider(tmp_path, name="groq", model="m", base_url="u", api_key="k")
    assert pconfig.delete_provider(tmp_path, "noexiste") is False
    assert pconfig.delete_provider(tmp_path, saved.id) is True
    assert pconfig.list_providers(tmp_path) == []


def test_update_provider_keeps_key_when_empty(tmp_path: Path) -> None:
    saved = pconfig.save_provider(tmp_path, name="groq", model="m", base_url="u", api_key="secret")
    updated = pconfig.update_provider(tmp_path, saved.id, model="m2", api_key="")
    assert updated.model == "m2"
    assert read_global()["providers"][0]["api_key"] == "secret"


def test_api_key_for_base_url(tmp_path: Path) -> None:
    pconfig.save_provider(tmp_path, name="groq", model="m", base_url="https://x/v1", api_key="k")
    assert pconfig.api_key_for_base_url(tmp_path, "https://x/v1") == "k"
    assert pconfig.api_key_for_base_url(tmp_path, "https://otra/v1") == ""


def test_runtime_ignores_project_level_keys(tmp_path: Path) -> None:
    write(
        tmp_path / pconfig.CONFIG_PATH,
        {"name": "openai", "model": "gpt", "base_url": "https://x/v1", "api_key": "k"},
    )
    provider = pconfig.active_provider(tmp_path)
    assert provider is None
    assert "api_key" not in pconfig.runtime_config(tmp_path)


def test_legacy_key_migration_requires_confirmation(tmp_path: Path) -> None:
    write(
        tmp_path / pconfig.CONFIG_PATH,
        {
            "providers": [{"id": "legacy", "name": "openai", "model": "gpt", "base_url": "https://example.test/v1", "api_key": "test-only-key"}],
            "llm": {"provider_id": "legacy"},
        },
    )
    summary = pconfig.migrate_legacy_credentials(tmp_path)
    assert summary["providers"] == 1
    assert read(tmp_path)["providers"][0]["api_key"] == "test-only-key"
    assert not pconfig.global_config_path().exists()

    pconfig.migrate_legacy_credentials(tmp_path, confirmed=True)

    assert "providers" not in read(tmp_path)
    assert read_global()["providers"][0]["api_key"] == "test-only-key"
    runtime = pconfig.runtime_config(tmp_path)
    assert runtime["providers"][0]["api_key"] == "test-only-key"


def test_find_provider_or_raise_lists_ids(tmp_path: Path) -> None:
    saved = pconfig.save_provider(tmp_path, name="groq", model="m", base_url="u", api_key="k")
    assert pconfig.find_provider_or_raise(tmp_path, saved.id).id == saved.id
    with pytest.raises(KeyError):
        pconfig.find_provider_or_raise(tmp_path, "nope")
