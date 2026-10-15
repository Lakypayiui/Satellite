"""Tests del log de auditoría append-only (Fase 1)."""

from pathlib import Path

from satellite.kernel.audit import AuditLog


def test_record_and_read(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit")
    entry = log.record("agent_created", actor="user", target="docs_writer", details={"k": 1})
    assert entry["action"] == "agent_created"
    assert entry["timestamp"]
    entries = log.read_all()
    assert len(entries) == 1
    assert entries[0]["details"] == {"k": 1}


def test_chain_and_integrity(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit")
    first = log.record("a")
    second = log.record("b")
    assert first["prev_hash"] == ""
    assert second["prev_hash"] == first["hash"]
    assert log.verify_integrity() is True


def test_integrity_detects_tampering(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit")
    log.record("a")
    log.record("b")
    # manipular una línea por detrás del log
    lines = log.path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"action": "a"', '"action": "hacked"')
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_integrity() is False


def test_append_only_grows(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit")
    for i in range(5):
        log.record(f"action_{i}")
    assert len(log.read_all()) == 5
    assert log.last_hash()


def test_record_requires_action(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit")
    try:
        log.record("")
    except ValueError:
        return
    raise AssertionError("debía lanzar ValueError")


def test_tolerant_to_corrupt_lines(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit")
    log.record("a")
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write("no es json\n")
    assert len(log.read_all()) == 1
