"""Auditoría de control de escritorio: metadatos, sin contenido, integridad."""

import json

from satellite.desktop_control import audit_desktop_action, audit_log_for, ref_hash
from satellite.kernel.audit import AuditLog


def test_ref_hash_is_stable_short_and_hides_raw() -> None:
    assert ref_hash("e12") == ref_hash("e12")
    assert len(ref_hash("e12")) == 16
    assert ref_hash("") == ""


def test_audit_records_metadata_without_ui_content(tmp_path) -> None:
    log = AuditLog(tmp_path / "audit")
    entry = audit_desktop_action(
        log,
        action="desktop_act",
        app="Demo",
        ref="e12",
        action_type="set_value",
        snapshot_id="s1",
        ok=True,
        code="ok",
        session_id="sess",
    )
    assert entry is not None
    details = entry["details"]
    assert details["app"] == "Demo"
    assert details["ref_hash"] == ref_hash("e12")
    assert details["action_type"] == "set_value"
    assert details["snapshot_id"] == "s1"
    for forbidden in ("name", "value", "text", "label", "content"):
        assert forbidden not in details
    # The raw ref must not appear in the logged metadata. Integrity-chain hashes are
    # random hex and may contain any short substring by chance, so they are excluded.
    logged_metadata = {
        key: value
        for key, value in entry.items()
        if key not in ("hash", "prev_hash")
    }
    assert "e12" not in json.dumps(logged_metadata)
    assert log.verify_integrity() is True


def test_audit_tamper_is_detected(tmp_path) -> None:
    log = AuditLog(tmp_path / "audit")
    audit_desktop_action(log, action="desktop_observe", app="Demo")
    original = log.path.read_text(encoding="utf-8")
    log.path.write_text(original.replace('"Demo"', '"Otra"'), encoding="utf-8")
    assert log.verify_integrity() is False


def test_audit_log_for_uses_global_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path))
    log = audit_log_for()
    assert log.root == tmp_path / "audit"
