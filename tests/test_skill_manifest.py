"""Tests del manifest de skill (Fase 3)."""

import pytest

from satellite.core.errors import SpecValidationError
from satellite.skills.models import SkillManifest, SkillStats, SkillStatus


def make_manifest() -> SkillManifest:
    return SkillManifest(
        id="count_words",
        description="Cuenta las palabras de un texto",
        tags=("texto", "conteo"),
        language="python",
        entrypoint="main.py",
        inputs={"type": "object"},
        outputs={"type": "object"},
        examples=("contar palabras de un párrafo",),
        created_by="agent_creator",
    )


def test_manifest_roundtrip() -> None:
    manifest = make_manifest()
    assert SkillManifest.from_dict(manifest.to_dict()) == manifest


def test_manifest_invalid_id() -> None:
    with pytest.raises(SpecValidationError):
        SkillManifest(id="Count Words", description="x")


def test_manifest_requires_description() -> None:
    with pytest.raises(SpecValidationError):
        SkillManifest(id="ok_id", description="")


def test_manifest_requires_entrypoint() -> None:
    with pytest.raises(SpecValidationError):
        SkillManifest(id="ok_id", description="d", entrypoint="")


def test_stats_roundtrip_and_with_stats() -> None:
    manifest = make_manifest()
    stats = SkillStats(uses=3, successes=2, failures=1, creation_tokens=50)
    updated = manifest.with_stats(stats)
    assert updated.stats.uses == 3
    assert SkillStats.from_dict(stats.to_dict()) == stats


def test_status_roundtrip() -> None:
    manifest = make_manifest().with_status(SkillStatus.DEPRECATED)
    assert manifest.status is SkillStatus.DEPRECATED
    assert SkillManifest.from_dict(manifest.to_dict()).status is SkillStatus.DEPRECATED


def test_from_dict_sets_created_at() -> None:
    manifest = SkillManifest.from_dict({"id": "a_b", "description": "d"})
    assert manifest.created_at
