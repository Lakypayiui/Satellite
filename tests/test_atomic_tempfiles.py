"""Atomic writes and orphan-temp cleanup."""

import os
import time

import pytest

from satellite.core.atomic import atomic_write_text
from satellite.core.tempfiles import cleanup_orphan_temps


def test_atomic_write_leaves_no_temp(tmp_path) -> None:
    target = tmp_path / "config.json"
    atomic_write_text(target, '{"ok": true}')

    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_removes_temp_when_replace_fails(tmp_path, monkeypatch) -> None:
    target = tmp_path / "config.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("satellite.core.atomic.os.replace", fail_replace)

    with pytest.raises(OSError):
        atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_cleanup_removes_only_old_temps(tmp_path) -> None:
    nested = tmp_path / "state"
    nested.mkdir()
    old = nested / "old.json.tmp"
    recent = nested / "recent.json.tmp"
    old.write_text("x", encoding="utf-8")
    recent.write_text("y", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 7200, now - 7200))
    os.utime(recent, (now - 60, now - 60))

    removed = cleanup_orphan_temps([tmp_path], max_age_s=3600, now=now)

    assert old in removed
    assert not old.exists()
    assert recent.exists()


def test_cleanup_tolerates_missing_roots(tmp_path) -> None:
    assert cleanup_orphan_temps([tmp_path / "missing"]) == []
