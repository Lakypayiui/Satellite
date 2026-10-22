"""Tests de migraciones de esquema, snapshots/rollback y lifecycle (Fase 8)."""

from pathlib import Path

import pytest

from satellite.core.errors import SpecValidationError
from satellite.kernel import migrations
from satellite.persistence.store import FileStore
from satellite.skills.models import SkillManifest


def test_schema_version_is_four() -> None:
    # Guardia explícita: subir la versión es una decisión consciente (v4: lifecycle).
    assert migrations.SCHEMA_VERSION == 4


def test_current_version_starts_at_zero(tmp_path: Path) -> None:
    assert migrations.current_version(tmp_path / ".satellite") == 0


# ---------------------------------------------------------------- lifecycle


def test_skill_manifest_lifecycle_defaults_to_persistent() -> None:
    manifest = SkillManifest(id="mi_skill", description="hace algo")
    assert manifest.lifecycle == "persistent"
    assert SkillManifest.from_dict(manifest.to_dict()).lifecycle == "persistent"


def test_skill_manifest_lifecycle_roundtrip() -> None:
    manifest = SkillManifest(id="mi_skill", description="hace algo", lifecycle="ephemeral")
    assert manifest.to_dict()["lifecycle"] == "ephemeral"
    assert SkillManifest.from_dict(manifest.to_dict()).lifecycle == "ephemeral"


def test_skill_manifest_tolerates_missing_lifecycle() -> None:
    """Un manifest antiguo (sin el campo) se carga como persistente."""
    old = {"id": "mi_skill", "description": "hace algo", "entrypoint": "main.py"}
    assert SkillManifest.from_dict(old).lifecycle == "persistent"


def test_skill_manifest_rejects_unknown_lifecycle() -> None:
    with pytest.raises(SpecValidationError):
        SkillManifest(id="mi_skill", description="hace algo", lifecycle="inventado")


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


@pytest.mark.parametrize("name", ["../../afuera", "..", "."])
def test_restore_snapshot_rejects_names_outside_the_snapshots_directory(
    tmp_path: Path, name: str
) -> None:
    """Un nombre con traversal no puede convertir una carpeta cualquiera en snapshot."""
    root = tmp_path / ".satellite"
    migrations.migrate(root)
    fuera = tmp_path / "afuera"
    fuera.mkdir()
    (fuera / "secreto.txt").write_text("no debe copiarse", encoding="utf-8")
    antes = sorted(path.name for path in root.iterdir())

    with pytest.raises(FileNotFoundError):
        migrations.restore_snapshot(root, name)

    assert sorted(path.name for path in root.iterdir()) == antes
    assert not (root / "secreto.txt").exists()


def test_restore_snapshot_rejects_an_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / ".satellite"
    migrations.migrate(root)
    fuera = tmp_path / "afuera"
    fuera.mkdir()
    (fuera / "secreto.txt").write_text("no debe copiarse", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        migrations.restore_snapshot(root, str(fuera))

    assert not (root / "secreto.txt").exists()


def test_migrate_agent_specs_adds_lifecycle(tmp_path: Path) -> None:
    """v3→v4: los specs guardados sin `lifecycle` se normalizan (idempotente)."""
    import json

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "viejo.json").write_text(
        json.dumps({"id": "viejo", "name": "Viejo", "capabilities": ["a"]}),
        encoding="utf-8",
    )
    (agents / "nuevo.json").write_text(
        json.dumps({"id": "nuevo", "lifecycle": "ephemeral"}), encoding="utf-8"
    )
    (agents / "roto.json").write_text("{no soy json", encoding="utf-8")

    assert migrations.migrate_agent_specs(agents) == ["viejo"]
    migrado = json.loads((agents / "viejo.json").read_text(encoding="utf-8"))
    assert migrado["lifecycle"] == "persistent"
    assert migrado["name"] == "Viejo"  # no se toca nada más
    # no pisa lo que ya lo traía, no revienta con el corrupto y es idempotente
    assert migrations.migrate_agent_specs(agents) == []
    assert (
        json.loads((agents / "nuevo.json").read_text(encoding="utf-8"))["lifecycle"]
        == "ephemeral"
    )
    # y sin directorio tampoco falla
    assert migrations.migrate_agent_specs(tmp_path / "no-existe") == []


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
