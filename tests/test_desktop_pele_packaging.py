"""Pele debe ir fuera de app.asar para que el backend Python pueda importarlo."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"


def test_packaged_backend_receives_a_real_pele_module_path() -> None:
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    assert {
        "from": "node_modules/pele",
        "to": "pele",
        "filter": ["**/*"],
    } in package["build"]["extraResources"]

    main = (DESKTOP / "electron" / "main.ts").read_text(encoding="utf-8")
    assert 'SATELLITE_PELE_PATH: path.join(process.resourcesPath, "pele")' in main

    backend = (DESKTOP / "electron" / "backend.ts").read_text(encoding="utf-8")
    assert "...this.launch.env," in backend
