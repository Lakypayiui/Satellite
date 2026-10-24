"""Abrir la app sin proyecto no construye un runtime sobre la carpeta personal."""

from pathlib import Path

from satellite.desktop.backend import DesktopBackend
from satellite.events.bus import EventBus
from satellite.tui.session import Session


def test_implicit_home_does_not_start_runtime_until_project_is_chosen(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.chdir(user_home)
    calls: list[Path] = []

    class RecordingSession(Session):
        def runtime(self, **_kwargs):
            calls.append(self.project_root)
            return (None, EventBus(), None, None)

    backend = DesktopBackend(home=tmp_path / "data", session_factory=RecordingSession)

    # Son las dos puertas de entrada que Electron usa durante la carga inicial.
    assert not backend.bridge().attached
    assert backend.status()["runtime_ready"] is False
    assert backend.status()["project_explicit"] is False
    assert calls == []
    assert not (user_home / ".satellite" / "context" / "index.json").exists()

    project = tmp_path / "project"
    project.mkdir()
    status = backend.open_project(project)

    assert status["project_explicit"] is True
    assert status["runtime_ready"] is True
    assert calls == [project]
    assert backend.bridge().attached
    assert calls == [project]


def test_initial_git_queries_do_not_scan_the_implicit_home(tmp_path, monkeypatch):
    from satellite import gitops

    monkeypatch.chdir(tmp_path)

    def unexpected_git(*_args, **_kwargs):
        raise AssertionError("no se debe consultar Git sin proyecto elegido")

    monkeypatch.setattr(gitops, "_git", unexpected_git)
    backend = DesktopBackend(home=tmp_path / "data")

    assert backend.git_status()["is_repo"] is False
    assert backend.worktrees()["worktrees"] == []
