"""Tests de migraciones de esquema y snapshots/rollback (Fase 8)."""

from pathlib import Path

from satellite.kernel import migrations
from satellite.persistence.store import FileStore


def test_current_version_starts_at_zero(tmp_path: Path) -> None:
    assert migrations.current_version(tmp_path / ".satellite") == 0


def test_migrate_sets_version_and_creates_dirs(tmp_path: Path) -> None:
    root = tmp_path / ".satellite"
    applied = migrations.migrate(root)
    assert applied == list(range(1, migrations.SCHEMA_VERSION + 1))
    assert migrations.current_version(root) == migrations.SCHEMA_VERSION
    assert (root / "metrics").is_dir()
    # idempotente
    assert migrations.migrate(root) == []


def test_snapshot_and_restore(tmp_path: Path) -> None:
    root = tmp_path / ".satellite"
    migrations.migrate(root)
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "a.json").write_text('{"v": 1}', encoding="utf-8")

    snapshot = migrations.create_snapshot(root, label="before")
    assert snapshot.is_dir()
    assert (snapshot / "state" / "a.json").exists()

    (root / "state" / "a.json").write_text('{"v": 2}', encoding="utf-8")
    (root / "state" / "b.json").write_text("{}", encoding="utf-8")

    migrations.restore_snapshot(root, snapshot.name)
    assert (root / "state" / "a.json").read_text(encoding="utf-8") == '{"v": 1}'
    assert not (root / "state" / "b.json").exists()
    # el propio snapshot se conserva
    assert (root / "snapshots" / snapshot.name).is_dir()


def test_list_snapshots(tmp_path: Path) -> None:
    root = tmp_path / ".satellite"
    migrations.migrate(root)
    assert migrations.list_snapshots(root) == []
    migrations.create_snapshot(root, label="one")
    migrations.create_snapshot(root, label="two")
    assert len(migrations.list_snapshots(root)) == 2


def test_restore_missing_snapshot_raises(tmp_path: Path) -> None:
    root = tmp_path / ".satellite"
    migrations.migrate(root)
    try:
        migrations.restore_snapshot(root, "nope")
    except FileNotFoundError:
        return
    raise AssertionError("debía lanzar FileNotFoundError")


def test_file_store_schema_and_snapshot(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    assert store.schema_version() < migrations.SCHEMA_VERSION
    applied = store.migrate()
    assert applied
    assert store.schema_version() == migrations.SCHEMA_VERSION
    snapshot = store.snapshot("pre")
    assert snapshot.exists()
    assert snapshot.name in {path.name for path in store.list_snapshots()}
    store.restore(snapshot.name)
