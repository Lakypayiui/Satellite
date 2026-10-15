"""Migraciones de esquema y lifecycle de skills (F4)."""

from pathlib import Path

import pytest

from satellite.core.errors import SpecValidationError
from satellite.kernel import migrations
from satellite.persistence.store import FileStore
from satellite.skills.models import SkillManifest


def test_schema_version_is_three() -> None:
    assert migrations.SCHEMA_VERSION == 3


def test_migrate_bumps_creates_subdirs_and_is_idempotent(tmp_path: Path) -> None:
    assert migrations.current_version(tmp_path) == 0
    applied = migrations.migrate(tmp_path)
    assert applied == [1, 2, 3]
    assert migrations.current_version(tmp_path) == 3
    for sub in migrations.REQUIRED_SUBDIRS:
        assert (tmp_path / sub).is_dir()
    assert migrations.migrate(tmp_path) == []


def test_current_version_is_tolerant(tmp_path: Path) -> None:
    (tmp_path / migrations.VERSION_FILE).write_text("no-int", encoding="utf-8")
    assert migrations.current_version(tmp_path) == 0
    assert migrations.current_version(tmp_path / "noexiste") == 0


def test_migrate_accepts_a_lower_target(tmp_path: Path) -> None:
    assert migrations.migrate(tmp_path, target=2) == [1, 2]
    assert migrations.current_version(tmp_path) == 2
    assert migrations.migrate(tmp_path) == [3]


def test_filestore_migrate_snapshot_and_restore(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    assert store.schema_version() == 0
    assert store.migrate() == [1, 2, 3]
    assert store.schema_version() == 3

    marker = store.root / "tasks" / "t.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"ok": true}', encoding="utf-8")
    name = store.snapshot("antes").name
    assert any(path.name == name for path in store.list_snapshots())

    marker.write_text("{corrupto", encoding="utf-8")
    store.restore(name)
    assert marker.read_text(encoding="utf-8") == '{"ok": true}'


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
