from satellite_py.registry import AgentDescriptor
from satellite_py.store import AgentStore


def test_store_initializes_layout_and_defaults(tmp_path):
    store = AgentStore(tmp_path)
    store.initialize()
    for name in ("config", "registry", "agents", "context", "executions"):
        assert (tmp_path / ".satellite" / name).is_dir()
    assert (tmp_path / ".satellite" / "config" / "config.json").is_file()
    assert store.load_registry().list_agents() == []


def test_store_roundtrips_descriptors(tmp_path):
    store = AgentStore(tmp_path)
    descriptor = AgentDescriptor(id=4, name="demo", capabilities=["demo"], library_path="demo.dll", enabled=False)
    store.save_descriptor(descriptor)
    loaded = store.load_descriptors()
    assert len(loaded) == 1
    assert loaded[0].library_path == "demo.dll"
    assert not loaded[0].enabled


def test_store_ignores_corrupt_agent_files(tmp_path):
    store = AgentStore(tmp_path)
    store.ensure_dirs()
    (tmp_path / ".satellite" / "agents" / "agent_9.json").write_text("{broken", encoding="utf-8")
    assert store.load_descriptors() == []
