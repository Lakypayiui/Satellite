"""Detección automática de la ventana de contexto.

Motivo: con `deepseek` la barra mostraba 40% porque al arrancar (sin conversación) se
medía contra la tabla de modelos (64k) en vez de la ventana que el proveedor ya había
publicado (1M); y porque un fallo de red se guardaba como «no la publica» para siempre.
"""

import json
from pathlib import Path

from satellite.context import compaction


def _config(project: Path, *, model: str, base_url: str) -> None:
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        json.dumps({"name": "openai", "model": model, "base_url": base_url, "api_key": "k"}),
        encoding="utf-8",
    )


def _cache(project: Path, values: dict) -> None:
    path = project / ".satellite" / "state" / "context_window.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")


def test_the_known_window_is_used_without_a_conversation(tmp_path: Path, monkeypatch) -> None:
    """Al arrancar (sin chat) la barra debe leer la ventana ya conocida, no la tabla.

    ``SATELLITE_HOME`` aísla del proveedor global activo del usuario (``active_connection``
    resuelve ``llm.provider_id``/``active_id`` desde el almacén global): sin aislarlo, el
    modelo/endpoint reales se filtraban y se consultaba la red, devolviendo 1_000_000.
    """
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(compaction, "_ASKED", set())
    # La ventana ya está cacheada: no debe haber ninguna consulta de red.
    monkeypatch.setattr(
        compaction,
        "discover_window_result",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no debe consultar la red")),
    )
    _config(tmp_path, model="deepseek-flash", base_url="https://api.deepseek.com/v1")
    _cache(tmp_path, {"deepseek-flash": 1_048_576})

    from satellite.tui.session import Session

    status = Session(tmp_path, home=tmp_path / "home").status()

    assert status.context_window == 1_048_576
    assert status.context_source == "proveedor"


def test_a_failed_query_is_not_remembered(tmp_path: Path, monkeypatch) -> None:
    """Si el proveedor no contesta, no se guarda 0: se reintentará en otra sesión."""
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(compaction, "_ASKED", set())
    _config(tmp_path, model="m", base_url="https://ejemplo/v1")
    monkeypatch.setattr(compaction, "discover_window_result", lambda **kw: (None, False))

    usage = compaction.project_usage(tmp_path)

    assert compaction.cached_window(tmp_path, "m") is None
    assert usage.window_source == "defecto"


def test_an_answer_without_a_window_is_remembered(tmp_path: Path, monkeypatch) -> None:
    """Si responde pero no la publica, se recuerda 0 y no se repite la consulta."""
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(compaction, "_ASKED", set())
    _config(tmp_path, model="m2", base_url="https://ejemplo/v1")
    monkeypatch.setattr(compaction, "discover_window_result", lambda **kw: (None, True))

    compaction.project_usage(tmp_path)

    assert compaction.cached_window(tmp_path, "m2") == 0


def test_forget_window_only_removes_that_model(tmp_path: Path) -> None:
    _cache(tmp_path, {"uno": 1000, "dos": 2000})

    compaction.forget_window(tmp_path, "uno")

    assert compaction.cached_window(tmp_path, "uno") is None
    assert compaction.cached_window(tmp_path, "dos") == 2000


def test_one_query_per_session_and_model(monkeypatch) -> None:
    """Un proveedor caído no provoca una consulta por cada refresco de la barra."""
    monkeypatch.setattr(compaction, "_ASKED", set())

    assert compaction._already_asked("m", "u") is False
    assert compaction._already_asked("m", "u") is True
    assert compaction._already_asked("otro", "u") is False
