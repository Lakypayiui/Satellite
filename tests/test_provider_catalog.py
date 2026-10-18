"""Catálogo de proveedores: forma, familias y búsqueda."""

from satellite.config import catalog


def test_catalog_has_at_least_fifty_entries() -> None:
    assert len(catalog.CATALOG) >= 50


def test_catalog_keys_are_unique() -> None:
    keys = catalog.keys()
    assert len(keys) == len(set(keys))


def test_catalog_families_are_only_openai_and_anthropic() -> None:
    families = {entry.family for entry in catalog.CATALOG}
    assert families <= set(catalog.FAMILIES)


def test_every_entry_has_base_url_and_label() -> None:
    for entry in catalog.CATALOG:
        assert entry.base_url.strip().startswith("http"), entry.key
        assert entry.label.strip(), entry.key
        assert entry.group in catalog.GROUPS, entry.key


def test_anthropic_entry_uses_anthropic_family() -> None:
    entry = catalog.get("anthropic")
    assert entry is not None
    assert entry.family == catalog.ANTHROPIC
    assert entry.base_url == "https://api.anthropic.com"


def test_local_entries_do_not_need_api_key() -> None:
    locals_ = [e for e in catalog.CATALOG if e.group == "Local"]
    assert len(locals_) >= 8
    assert all("localhost" in entry.base_url for entry in locals_)
    assert all(entry.needs_key is False for entry in locals_)
    assert all(entry.needs_key is True for entry in catalog.CATALOG if entry.group != "Local")


def test_entries_needing_edit_are_flagged() -> None:
    flagged = [e for e in catalog.CATALOG if e.needs_edit]
    assert flagged
    assert all("<" in entry.base_url for entry in flagged)
    assert all(entry.group == "Cloud (editar URL)" for entry in flagged)


def test_common_providers_are_present() -> None:
    for key in ("openai", "anthropic", "gemini", "deepseek", "groq", "openrouter", "ollama"):
        assert catalog.get(key) is not None, key


def test_commandcode_entries_use_documented_openai_and_anthropic_roots() -> None:
    openai = catalog.get("commandcode")
    anthropic = catalog.get("commandcode-anthropic")
    assert openai is not None
    assert openai.family == catalog.OPENAI
    assert openai.base_url == "https://api.commandcode.ai/provider/v1"
    assert anthropic is not None
    assert anthropic.family == catalog.ANTHROPIC
    assert anthropic.base_url == "https://api.commandcode.ai/provider"


def test_commandcode_entries_are_in_the_last_catalogue_group() -> None:
    grouped = catalog.by_group()
    assert catalog.GROUPS[-1] == "Pasarelas"
    assert [entry.key for entry in grouped["Pasarelas"]] == [
        "commandcode", "commandcode-anthropic"
    ]


def test_find_matches_key_label_and_group() -> None:
    assert [e.key for e in catalog.find("groq")] == ["groq"]
    assert any(e.key == "moonshot" for e in catalog.find("kimi"))
    assert len(catalog.find("local")) >= 8
    assert catalog.find("") == list(catalog.CATALOG)
    assert catalog.find("no-existe-este-proveedor") == []


def test_by_group_covers_every_entry() -> None:
    grouped = catalog.by_group()
    total = sum(len(entries) for entries in grouped.values())
    assert total == len(catalog.CATALOG)


def test_unknown_key_returns_none() -> None:
    assert catalog.get("nope") is None
