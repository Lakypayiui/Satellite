"""Tests de contrato de modelos de contexto."""

import pytest

from satellite.core.context_models import (
    ContextBundle,
    ContextLimits,
    ContextRef,
    ContextRefKind,
    ContextSection,
    ContextSnapshot,
)
from satellite.core.errors import ContractError


def test_context_ref_valid_kinds() -> None:
    for kind in ContextRefKind:
        ref = ContextRef(kind=kind, value="src/x.py")
        assert ref.kind is kind


def test_context_ref_invalid_kind_raises() -> None:
    with pytest.raises(ContractError):
        ContextRef(kind="raro", value="x")  # type: ignore[arg-type]


def test_context_ref_empty_value_raises() -> None:
    with pytest.raises(ContractError):
        ContextRef(kind=ContextRefKind.PATH, value="")


def test_bundle_total_chars_and_tokens() -> None:
    bundle = ContextBundle(
        sections=(
            ContextSection(ref=ContextRef(ContextRefKind.PATH, "a.py"), content="hola", tokens=2),
            ContextSection(ref=ContextRef(ContextRefKind.PATH, "b.py"), content="mundo", tokens=3),
        )
    )
    assert bundle.total_chars == 9
    assert bundle.total_tokens == 5


def test_limits_violations_ok() -> None:
    bundle = ContextBundle(
        sections=(ContextSection(ref=ContextRef(ContextRefKind.PATH, "a"), content="ab"),)
    )
    limits = ContextLimits(max_total_chars=100, max_sections=2)
    assert limits.violations(bundle) == []


def test_limits_violations_total_chars() -> None:
    bundle = ContextBundle(
        sections=(ContextSection(ref=ContextRef(ContextRefKind.PATH, "a"), content="x" * 70000),)
    )
    limits = ContextLimits(max_total_chars=60000)
    v = limits.violations(bundle)
    assert len(v) == 1
    assert "max_total_chars=60000 excedido: 70000" in v[0]


def test_limits_violations_max_sections() -> None:
    bundle = ContextBundle(
        sections=(
            ContextSection(ref=ContextRef(ContextRefKind.PATH, "a"), content="x"),
            ContextSection(ref=ContextRef(ContextRefKind.PATH, "b"), content="y"),
        )
    )
    limits = ContextLimits(max_sections=1)
    v = limits.violations(bundle)
    assert len(v) == 1
    assert "max_sections=1 excedido: 2" in v[0]


def test_snapshot_round_trip() -> None:
    snap = ContextSnapshot(
        node_id="n1",
        refs=(ContextRef(ContextRefKind.PATH, "src/a.py"),),
        total_chars=1234,
        total_tokens=99,
    )
    restored = ContextSnapshot.from_dict(snap.to_dict())
    assert restored.node_id == "n1"
    assert restored.refs == snap.refs
    assert restored.total_chars == 1234
    assert restored.total_tokens == 99
    # created_at se autocompleta si vacío
    assert snap.created_at != ""
