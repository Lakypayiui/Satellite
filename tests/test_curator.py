"""Tests del SkillCurator (Fase 8)."""

import asyncio
import json
from pathlib import Path

from satellite.agents.curator import CuratorOperation, SkillCurator
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.approval.base import AutoApprovalPort, DenyAllApprovalPort
from satellite.providers.fake import FakeLLM
from satellite.skills.models import SkillManifest, SkillStatus
from satellite.skills.registry import SkillRegistry


def run(coro):
    return asyncio.run(coro)


def make_registry(tmp_path: Path) -> SkillRegistry:
    registry = SkillRegistry(tmp_path / "skills")
    registry.register(
        SkillManifest(id="csv_to_json", description="convierte csv a json", tags=("csv",)),
        "print(1)",
    )
    registry.register(
        SkillManifest(id="convert_csv_json", description="convierte csv en json", tags=("csv",)),
        "print(2)",
    )
    return registry


def make_curator(registry, script, *, approval=None, sink=None) -> SkillCurator:
    prompts = DefaultPromptRegistry()
    prompts.register_pair(
        "skill_curator",
        "T:{task} C:{context} F:{feedback} S:{output_schema}",
        "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
    )
    return SkillCurator(
        FakeLLM(script=script), registry, prompts, approval=approval, sink=sink
    )


def ops_json(operations) -> str:
    return json.dumps({"operations": operations})


def test_propose_parses_operations(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [{"content": ops_json([{"op": "retag", "targets": ["csv_to_json"], "proposal": {"tags": ["csv", "json"]}, "reason": "mejor tag"}])}])
    outcome = run(curator.propose("revisa"))
    assert outcome.kind == "proposed"
    assert outcome.operations[0].op == "retag"


def test_propose_filters_invalid_ops(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [{"content": ops_json([
        {"op": "create", "targets": ["x"], "reason": "no permitido"},
        {"op": "delete", "targets": [], "reason": "sin targets"},
    ])}])
    outcome = run(curator.propose())
    assert outcome.kind == "empty"


def test_propose_no_json(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [{"content": "nada"}])
    assert run(curator.propose()).kind == "error"


def test_apply_merge(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [])
    op = CuratorOperation(op="merge", targets=("csv_to_json", "convert_csv_json"), proposal={"keep_id": "csv_to_json", "description": "convierte csv a json cuando hay datos tabulares"}, reason="duplicadas")
    outcome = run(curator.apply((op,)))
    assert outcome.applied
    assert "convert_csv_json" not in {m.id for m in registry.all()}


def test_apply_edit_description(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [])
    op = CuratorOperation(op="edit", targets=("csv_to_json",), proposal={"description": "convierte csv a json cuando se necesita procesar tablas"}, reason="claridad")
    run(curator.apply((op,)))
    assert "tablas" in registry.get("csv_to_json").description


def test_apply_retag(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [])
    op = CuratorOperation(op="retag", targets=("csv_to_json",), proposal={"tags": ["csv", "json", "tablas"]}, reason="tags")
    run(curator.apply((op,)))
    assert registry.get("csv_to_json").tags == ("csv", "json", "tablas")


def test_apply_deprecate(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [])
    op = CuratorOperation(op="deprecate", targets=("convert_csv_json",), reason="duplicada")
    run(curator.apply((op,)))
    assert registry.get("convert_csv_json").status is SkillStatus.DEPRECATED


def test_apply_delete(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [])
    op = CuratorOperation(op="delete", targets=("convert_csv_json",), reason="sin usos")
    run(curator.apply((op,)))
    assert "convert_csv_json" not in {m.id for m in registry.all()}


def test_apply_skips_reserved_and_unknown(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [])
    ops = (
        CuratorOperation(op="delete", targets=("run_python",), reason="reservado"),
        CuratorOperation(op="delete", targets=("no_existe",), reason="fantasma"),
    )
    outcome = run(curator.apply(ops))
    assert outcome.applied == ()
    assert len(outcome.skipped) == 2


def test_apply_respects_approval(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [], approval=DenyAllApprovalPort())
    op = CuratorOperation(op="delete", targets=("convert_csv_json",), reason="x")
    outcome = run(curator.apply((op,), approval_port=DenyAllApprovalPort()))
    assert outcome.applied == ()
    assert "convert_csv_json" in {m.id for m in registry.all()}


def test_apply_approves_with_port(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(registry, [])
    op = CuratorOperation(op="delete", targets=("convert_csv_json",), reason="x")
    outcome = run(curator.apply((op,), approval_port=AutoApprovalPort()))
    assert outcome.applied
    assert "convert_csv_json" not in {m.id for m in registry.all()}


def test_flag_suspicious_emits_event(tmp_path: Path) -> None:
    from satellite.events.bus import EventBus

    registry = make_registry(tmp_path)
    bus = EventBus()
    seen: list = []
    bus.subscribe(seen.append)
    curator = make_curator(registry, [], sink=bus)
    op = CuratorOperation(op="flag_suspicious", targets=("csv_to_json",), reason="contenido raro")
    run(curator.apply((op,)))
    assert any(e.event_type.value == "policy_violation" for e in seen)


def test_run_proposes_and_applies(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    curator = make_curator(
        registry,
        [{"content": ops_json([{"op": "deprecate", "targets": ["convert_csv_json"], "reason": "dup"}])}],
    )
    outcome = run(curator.run("revisa", approval_port=AutoApprovalPort()))
    assert outcome.kind == "applied"
    assert outcome.applied
