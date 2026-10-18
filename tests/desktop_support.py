"""Dobles compartidos por los tests del backend de escritorio (auth, config, enlace).

``FakeSession`` lee de verdad ``.satellite/config/config.json`` (para probar la
redacción de la vista) pero no arma runtime, así que los tests no necesitan proveedor.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from satellite.desktop.backend import DesktopBackend

TOKEN = "token-local-de-prueba"


class FakeSession:
    """Sesión mínima: config real en disco, sin runtime."""

    def __init__(self, root, home=None) -> None:
        self.root = Path(root).resolve()
        self.home = Path(home) if home is not None else None
        self._config = self.root / ".satellite" / "config" / "config.json"

    def paths(self):
        (self.root / ".satellite").mkdir(parents=True, exist_ok=True)
        return SimpleNamespace()

    def config_data(self) -> dict:
        try:
            data = json.loads(self._config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def settings(self):
        return SimpleNamespace(
            policy_mode=SimpleNamespace(value="ask"), sandbox_level="n0"
        )

    def active_provider_id(self) -> str:
        return ""

    def status(self) -> dict:
        return {"project_root": str(self.root), "provider": "openai", "model": "m"}

    def runtime(self):
        return (None, None, None, None)


def make_backend(tmp_path: Path, factory=FakeSession, *, token: str = TOKEN) -> DesktopBackend:
    return DesktopBackend(
        tmp_path,
        token=token,
        home=tmp_path / "home",
        session_factory=factory,
        recents_path=tmp_path / "recents.json",
    )
