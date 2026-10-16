"""Cierre de 17a: allowlist de comandos por configuración y skills consideradas."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.core.events import EventType
from satellite.events.base import EventSink


def run(coro):
    return asyncio.run(coro)


class Sink(EventSink):
    """Sink que guarda lo emitido."""

    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        return None


def escribir_config(tmp_path: Path, data: dict) -> Path:
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    ruta = cfg / "config.json"
    ruta.write_text(json.dumps(data), encoding="utf-8")
    return ruta


# --- la allowlist de comandos ---


def test_without_a_config_there_is_no_allowlist(tmp_path: Path) -> None:
    """Vacío por defecto: todo comando se pregunta, no hay forma de «permitir todo»."""
    from satellite.kernel.policy import load_settings

    settings = load_settings(tmp_path / ".satellite" / "config" / "config.json")

    assert settings.allowed_commands == ()


def test_the_allowlist_is_read_tolerantly(tmp_path: Path) -> None:
    from satellite.kernel.policy import load_settings

    ruta = escribir_config(
        tmp_path,
        {"terminal": {"allowed_commands": ["git", "", 3, "git", "  python  ", None]}},
    )

    settings = load_settings(ruta)

    assert settings.allowed_commands == ("git", "python")  # sin basura ni repetidos


def test_a_broken_allowlist_is_ignored_and_fails_closed(tmp_path: Path) -> None:
    """Si el valor es basura, se ignora: el efecto es preguntar, no permitir."""
    from satellite.kernel.policy import load_settings

    ruta = escribir_config(tmp_path, {"terminal": {"allowed_commands": "git, curl"}})

    settings = load_settings(ruta)

    assert settings.allowed_commands == ()


def test_the_runtime_policy_honours_the_allowlist(tmp_path: Path) -> None:
    """El camino completo: config.json → política del runtime.

    El runtime exige un proveedor, así que se usa el `fake` de los tests: lo que se mira
    aquí es la política que sale de la configuración.
    """
    from satellite.cli.runtime_builder import build_runtime

    escribir_config(
        tmp_path,
        {"name": "fake", "model": "m", "terminal": {"allowed_commands": ["git"]}},
    )

    _orquestador, _bus, _store, _registro_agentes = build_runtime(tmp_path)
    policy = _orquestador.services.tools.policy

    assert policy.check_command(["git", "status"]) is True  # permitido sin preguntar
    assert policy.check_command(["curl", "http://ejemplo"]) is False  # lo demás, se pregunta


def test_listing_tools_uses_the_same_policy_as_the_runtime() -> None:
    """Antes aquí el terminal estaba vetado: listar y ejecutar decían cosas distintas."""
    from satellite.cli.runtime_builder import build_tool_registry

    policy = build_tool_registry().policy

    assert policy.allow_terminal is True
    assert policy.check_command(["git", "status"]) is False  # sin allowlist: se pregunta


# --- skills consideradas ---


def test_searching_skills_says_what_was_considered(tmp_path: Path) -> None:
    """Mirada no es usada: el evento lo dice y no inventa ninguna ejecución."""
    from satellite.skills.models import SkillManifest
    from satellite.skills.registry import SkillRegistry
    from satellite.tools.base import ToolContext
    from satellite.tools.skills import SearchSkillsTool

    bus = Sink()
    registry = SkillRegistry(tmp_path / "skills", sink=bus)
    registry.register(
        SkillManifest(id="echo_skill", description="Hace eco de la entrada", entrypoint="main.py"),
        "print('{}')",
    )
    ctx = ToolContext(project_root=tmp_path, sink=bus, skills=registry, task_id="t1")

    resultado = run(SearchSkillsTool().execute({"query": "eco", "top_k": 3}, ctx))

    assert resultado.success is True
    buscados = [evento for evento in bus.events if evento.event_type is EventType.SKILL_SEARCHED]
    assert len(buscados) == 1
    assert buscados[0].payload["query"] == "eco"
    assert [item["skill_id"] for item in buscados[0].payload["considered"]] == ["echo_skill"]
    # y ninguna se declara usada por el hecho de haberla mirado
    usadas = [
        evento.event_type
        for evento in bus.events
        if evento.event_type in (EventType.SKILL_EXECUTED, EventType.SKILL_FAILED)
    ]
    assert usadas == []


def test_a_search_without_a_sink_does_not_explode(tmp_path: Path) -> None:
    from satellite.skills.models import SkillManifest
    from satellite.skills.registry import SkillRegistry
    from satellite.tools.base import ToolContext
    from satellite.tools.skills import SearchSkillsTool

    registry = SkillRegistry(tmp_path / "skills")
    registry.register(
        SkillManifest(id="echo_skill", description="Hace eco", entrypoint="main.py"),
        "print('{}')",
    )
    ctx = ToolContext(project_root=tmp_path, skills=registry)

    resultado = run(SearchSkillsTool().execute({"query": "eco"}, ctx))

    assert resultado.success is True
