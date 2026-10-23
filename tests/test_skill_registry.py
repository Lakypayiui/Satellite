"""Tests del registro de skills: registro, dedup, cuarentena y stats (Fase 3)."""

import hashlib
from pathlib import Path

import pytest

from satellite.core.errors import ToolError
from satellite.events.bus import EventBus
from satellite.skills.audit import code_digest
from satellite.skills.models import SkillManifest, SkillStatus
from satellite.skills.registry import SkillRegistry

SCRIPT = "import json, sys\nprint(json.dumps({'ok': True}))\n"


def make_manifest(skill_id: str = "count_words", description: str = "Cuenta palabras de un texto") -> SkillManifest:
    return SkillManifest(id=skill_id, description=description, entrypoint="main.py")


def test_register_creates_files(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    manifest = registry.register(make_manifest(), SCRIPT)
    assert manifest.hash
    assert (tmp_path / "count_words" / "manifest.json").exists()
    assert (tmp_path / "count_words" / "main.py").read_text(encoding="utf-8") == SCRIPT
    assert registry.get("count_words").id == "count_words"


def test_register_hashes_entrypoint_and_local_modules(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    skill_dir = tmp_path / "multi"
    skill_dir.mkdir()
    (skill_dir / "helper.py").write_text("valor = 1\n", encoding="utf-8")
    script = "from helper import valor\nprint(valor)\n"

    manifest = registry.register(make_manifest("multi"), script)

    assert manifest.hash == code_digest(skill_dir, manifest.entrypoint)
    assert manifest.hash != hashlib.sha256(script.encode("utf-8")).hexdigest()


def test_register_keeps_the_historical_hash_for_a_single_file(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    manifest = registry.register(make_manifest("single"), SCRIPT)
    assert manifest.hash == hashlib.sha256(SCRIPT.encode("utf-8")).hexdigest()


def test_the_script_is_written_byte_for_byte_on_every_platform(tmp_path: Path, monkeypatch) -> None:
    """Simula la traducción de saltos de línea de Windows: el archivo y el hash no cambian."""
    real_write_text = Path.write_text

    def windows_write_text(self, data, encoding=None, errors=None, newline=None):
        if newline is None:
            data = data.replace("\n", "\r\n")
        return real_write_text(self, data, encoding=encoding, errors=errors, newline="")

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    registry = SkillRegistry(tmp_path)
    manifest = registry.register(make_manifest("single"), SCRIPT)

    assert (tmp_path / "single" / "main.py").read_bytes() == SCRIPT.encode("utf-8")
    assert manifest.hash == hashlib.sha256(SCRIPT.encode("utf-8")).hexdigest()


def test_register_rejects_reserved_id(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    with pytest.raises(ToolError):
        registry.register(make_manifest("run_python"), SCRIPT)


def test_register_rejects_oversize_script(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path, max_script_bytes=5)
    with pytest.raises(ToolError):
        registry.register(make_manifest(), SCRIPT * 10)


def test_get_missing_raises(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    with pytest.raises(ToolError):
        registry.get("nope")


def test_find_similar_and_dedup(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    registry.register(make_manifest(), SCRIPT)
    assert registry.has_similar("contar palabras de un texto") is True
    assert registry.has_similar("zzqq unrelated token") is False


def test_load_all_tolerant_to_corrupt_manifest(tmp_path: Path) -> None:
    bus = EventBus()
    seen: list = []
    bus.subscribe(seen.append)
    registry = SkillRegistry(tmp_path, sink=bus)

    good = tmp_path / "good_skill"
    good.mkdir()
    (good / "manifest.json").write_text(
        '{"id": "good_skill", "description": "d", "entrypoint": "main.py"}', encoding="utf-8"
    )
    (good / "main.py").write_text(SCRIPT, encoding="utf-8")

    bad = tmp_path / "bad_skill"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json", encoding="utf-8")

    loaded = registry.load_all()
    assert [m.id for m in loaded] == ["good_skill"]
    assert not bad.exists()
    assert (tmp_path / "_quarantine" / "bad_skill").exists()
    assert any(e.event_type.value == "skill_quarantined" for e in seen)


def test_load_all_quarantines_missing_entrypoint(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    d = tmp_path / "no_script"
    d.mkdir()
    (d / "manifest.json").write_text(
        '{"id": "no_script", "description": "d", "entrypoint": "main.py"}', encoding="utf-8"
    )
    assert registry.load_all() == []
    assert (tmp_path / "_quarantine" / "no_script").exists()


def test_record_use_quarantines_after_failures(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path, max_consecutive_failures=3)
    registry.register(make_manifest(), SCRIPT)
    registry.record_use("count_words", False)
    registry.record_use("count_words", False)
    assert registry.get("count_words").stats.failures == 2
    manifest = registry.record_use("count_words", False)
    assert manifest.status is SkillStatus.QUARANTINED
    assert registry.count() == 0
    assert (tmp_path / "_quarantine" / "count_words").exists()


def test_record_use_resets_consecutive_on_success(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path, max_consecutive_failures=3)
    registry.register(make_manifest(), SCRIPT)
    registry.record_use("count_words", False)
    registry.record_use("count_words", True)
    assert registry.get("count_words").stats.consecutive_failures == 0


def test_remove(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    registry.register(make_manifest(), SCRIPT)
    registry.remove("count_words")
    assert registry.count() == 0
    assert not (tmp_path / "count_words").exists()


def test_reregister_archives_previous(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    registry.register(make_manifest(), SCRIPT)
    registry.register(make_manifest(), SCRIPT + "# v2\n")
    assert (tmp_path / "_history" / "count_words" / "1.0.0.json").exists()
