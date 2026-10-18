"""Compactación de contexto: ventana del modelo, medición, umbral y resumen."""

import asyncio
import json
from pathlib import Path

import pytest

from satellite.config import catalog as provider_catalog
from satellite.context.compaction import (
    DEFAULT_WINDOW,
    CompactionResult,
    ContextUsage,
    estimate_history,
    extractive_summary,
    last_real_tokens,
    measure,
    resolve_window,
    should_compact,
    strip_code_fences,
    summarize_turns,
)
from satellite.core.task import TaskResult, TaskStatus
from satellite.kernel.policy import CompactionConfig, Settings, load_settings
from satellite.observability.call_log import CallRecord, CallRecorder
from satellite.orchestration.chat import ChatSession
from satellite.providers.fake import FakeLLM
from satellite.tui.render import status_bar
from satellite.tui.session import SessionStatus


def run(coro):
    return asyncio.run(coro)


class FakeOrchestrator:
    """Orquestador mínimo que recuerda el historial que le llegó."""

    services = None

    def __init__(self) -> None:
        self.histories: list[list] = []

    async def execute(self, task):
        self.histories.append(list((task.metadata or {}).get("history") or []))
        return TaskResult(task_id="t1", status=TaskStatus.OK, summary="ok")


# ---------------------------------------------------------------- ventana


def test_window_resolution_order() -> None:
    # 1) config explícita manda
    assert resolve_window("cualquiera", config=CompactionConfig(window=4096)).tokens == 4096
    assert resolve_window("cualquiera", config=CompactionConfig(window=4096)).source == "config"
    # 2) tabla de modelos conocidos
    claude = resolve_window("claude-3-5-sonnet-latest")
    assert claude.tokens == 200_000 and claude.source == "modelo"
    assert resolve_window("gpt-4o-mini").tokens == 128_000
    # 3) entrada del catálogo por URL (proveedores locales)
    local = resolve_window("cualquiera", base_url="http://localhost:8080/v1")
    assert local.tokens == 32_768 and local.source == "catalogo"
    # 4) defecto
    fallback = resolve_window("modelo-desconocido-y-raro")
    assert fallback.tokens == DEFAULT_WINDOW and fallback.source == "defecto"


def test_local_catalog_entries_declare_a_window() -> None:
    for entry in provider_catalog.CATALOG:
        if entry.group == "Local":
            assert entry.context_window > 0, entry.key


def test_compaction_config_parsing(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"llm": {"context": {"provider": "x"}, '
        '"compaction": {"window": 32768, "compact_at": 0.7, "keep": 3}}}',
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.compaction.window == 32_768
    assert settings.compaction.compact_at == 0.7
    assert settings.compaction.keep == 3
    # `llm.context` es un ROL de modelo: no debe confundirse con la compactación
    assert load_settings(path).compaction.keep == 3


def test_compaction_config_is_tolerant_and_clamped(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"llm": {"compaction": {"window": "no", "compact_at": 5, "keep": 0}}}',
        encoding="utf-8",
    )
    config = load_settings(path).compaction
    assert config.window is None
    assert config.compact_at == 1.0  # acotado
    assert config.keep == 2  # mínimo sensato
    assert Settings().compaction.compact_at == 0.85


# ---------------------------------------------------------------- medición


def test_measure_uses_the_estimate_without_history() -> None:
    usage = measure(turns=(), pending_text="hola")
    assert usage.estimated_tokens >= 1
    assert usage.real_tokens == 0
    assert usage.used_tokens == usage.estimated_tokens
    assert usage.window_tokens == DEFAULT_WINDOW


def test_measure_takes_the_real_usage_when_available(tmp_path: Path) -> None:
    from satellite.observability.call_log import default_log_dir

    CallRecorder(default_log_dir(tmp_path)).record(CallRecord(model="m", input_tokens=9_000))
    assert last_real_tokens(tmp_path, "m") == 9_000
    usage = measure(turns=(), pending_text="hola", model="m", project_root=tmp_path)
    assert usage.real_tokens > 9_000
    assert usage.used_tokens == usage.real_tokens  # el real manda si es mayor


def test_last_real_tokens_filters_by_model(tmp_path: Path) -> None:
    from satellite.observability.call_log import default_log_dir

    recorder = CallRecorder(default_log_dir(tmp_path))
    recorder.record(CallRecord(model="otro", input_tokens=111))
    recorder.record(CallRecord(model="m", input_tokens=222))
    assert last_real_tokens(tmp_path, "m") == 222
    assert last_real_tokens(tmp_path, "inexistente") == 0


def test_should_compact_boundary() -> None:
    config = CompactionConfig(compact_at=0.85)
    assert should_compact(ContextUsage(used_tokens=84, window_tokens=100), config) is False
    assert should_compact(ContextUsage(used_tokens=85, window_tokens=100), config) is True
    assert should_compact(ContextUsage(used_tokens=120, window_tokens=100), config) is True


def test_usage_percent_and_dict() -> None:
    usage = ContextUsage(used_tokens=42, window_tokens=100, window_source="config")
    assert usage.percent == 42
    assert usage.to_dict()["window_source"] == "config"


# ---------------------------------------------------------------- resumen


def test_extractive_summary_is_deterministic_and_bounded() -> None:
    from satellite.orchestration.chat import Turn

    turns = [Turn("user", "pregunta " * 40), Turn("assistant", "respuesta")]
    summary = extractive_summary(turns)
    assert summary.startswith("Resumen de los turnos anteriores")
    assert "- usuario:" in summary and "- asistente:" in summary
    assert len(summary) <= 4_000
    assert extractive_summary([]) == ""


def test_summarize_uses_the_llm_when_available() -> None:
    from satellite.orchestration.chat import Turn

    turns = [Turn("user", "hazme un resumen")]
    provider = FakeLLM(script=[{"content": "resumen del modelo"}])
    text, method = run(summarize_turns(turns, provider))
    assert text == "resumen del modelo"
    assert method == "llm"


def test_summarize_falls_back_without_provider_or_on_failure() -> None:
    from satellite.orchestration.chat import Turn

    turns = [Turn("user", "necesito un resumen " * 12)]
    text, method = run(summarize_turns(turns, None))
    assert method == "determinista" and "usuario" in text
    broken = FakeLLM(script=[{"error": "sin conexión"}])
    text, method = run(summarize_turns(turns, broken))
    assert method == "determinista" and text


def test_summary_never_grows_the_context() -> None:
    """El resumen no puede ser más largo que el texto que sustituye."""
    from satellite.orchestration.chat import Turn

    turns = [Turn("user", "x" * 300)]
    text, _method = run(summarize_turns(turns, None))
    assert len(text) <= len("- user: " + "x" * 300)


def test_strip_code_fences() -> None:
    assert strip_code_fences("```\nresumen\n```") == "resumen"


# ---------------------------------------------------------------- ChatSession


def make_session(tmp_path: Path, *, window: int, keep: int = 2) -> ChatSession:
    settings = Settings(compaction=CompactionConfig(window=window, compact_at=0.85, keep=keep))
    return ChatSession(tmp_path, FakeOrchestrator(), settings=settings)


def test_compact_now_keeps_last_turns_and_adds_one_summary(tmp_path: Path) -> None:
    session = make_session(tmp_path, window=100_000, keep=2)
    for index in range(6):
        session.add("user", f"mensaje {index}: " + "contenido largo " * 25)
        session.add("assistant", f"respuesta {index}: " + "detalle extenso " * 25)

    result = run(session.compact_now())
    assert isinstance(result, CompactionResult)
    assert result.dropped == 10
    assert result.kept == 2
    assert result.method == "determinista"
    assert result.tokens_after < result.tokens_before
    turns = session.turns()
    assert len(turns) == 3
    assert turns[0].role == "summary"
    assert turns[1].content.startswith("mensaje 5")
    assert turns[2].content.startswith("respuesta 5")


def test_compact_now_does_nothing_when_it_would_not_shrink(tmp_path: Path) -> None:
    """Con mensajes diminutos el resumen no reduce: no se toca el historial."""
    session = make_session(tmp_path, window=100_000, keep=2)
    for index in range(6):
        session.add("user", f"m{index}")
        session.add("assistant", f"r{index}")
    before = session.turns()

    result = run(session.compact_now())
    assert result.method == "sin-efecto"
    assert result.dropped == 0
    assert session.turns() == before  # intacto


def test_compact_now_is_a_noop_when_history_is_short(tmp_path: Path) -> None:
    session = make_session(tmp_path, window=100_000, keep=6)
    session.add("user", "uno")
    result = run(session.compact_now())
    assert result.dropped == 0 and result.method == "sin-cambios"


def test_compact_now_uses_the_llm_when_there_is_a_provider(tmp_path: Path) -> None:
    session = make_session(tmp_path, window=100_000, keep=1)
    session._orchestrator_services = None  # no aplica: usamos un stub abajo
    provider = FakeLLM(script=[{"content": "resumen con modelo"}])
    session.provider = lambda: provider  # type: ignore[method-assign]
    for index in range(4):
        session.add("user", f"m{index}")
    result = run(session.compact_now())
    assert result.method == "llm"
    assert session.turns()[0].content == "resumen con modelo"


def test_auto_compaction_triggers_at_the_threshold(tmp_path: Path) -> None:
    orchestrator = FakeOrchestrator()
    settings = Settings(compaction=CompactionConfig(window=120, compact_at=0.85, keep=2))
    session = ChatSession(tmp_path, orchestrator, settings=settings)
    for index in range(8):
        session.add("user", "mensaje largo " * 20 + str(index))
        session.add("assistant", "respuesta larga " * 20)

    assert session.should_compact("otra cosa") is True
    reply = run(session.send("y ahora qué"))
    assert reply.compaction is not None
    assert reply.compaction["dropped"] > 0
    # el historial que recibió el orquestador ya venía compactado
    assert len(orchestrator.histories[0]) <= 3
    assert session.turns()[0].role == "summary"


def test_auto_compaction_does_not_trigger_with_a_big_window(tmp_path: Path) -> None:
    orchestrator = FakeOrchestrator()
    settings = Settings(compaction=CompactionConfig(window=1_000_000, keep=2))
    session = ChatSession(tmp_path, orchestrator, settings=settings)
    for index in range(6):
        session.add("user", f"m{index}")

    reply = run(session.send("hola"))
    assert reply.compaction is None
    assert session.should_compact("hola") is False


def test_usage_is_reported_with_the_window_source(tmp_path: Path) -> None:
    session = make_session(tmp_path, window=50_000)
    session.add("user", "hola")
    usage = session.context_usage("qué tal")
    assert usage.window_tokens == 50_000
    assert usage.window_source == "config"
    assert usage.used_tokens > 0


def test_estimate_history_counts_all_turns(tmp_path: Path) -> None:
    session = make_session(tmp_path, window=100_000)
    session.add("user", "x" * 400)
    assert estimate_history(session.turns()) >= 100


# ---------------------------------------------------------------- descubrimiento


def test_resolve_window_prefers_the_provider_data() -> None:
    discovered = resolve_window("modelo-raro", discovered=8_192)
    assert discovered.tokens == 8_192
    assert discovered.source == "proveedor"
    # la config explícita sigue mandando sobre todo lo demás
    forced = resolve_window("modelo-raro", config=CompactionConfig(window=4_096), discovered=8_192)
    assert forced.tokens == 4_096 and forced.source == "config"


def test_fetch_models_reads_the_context_metadata(monkeypatch) -> None:
    from satellite.config.models import fetch_models

    payload = {
        "data": [
            {"id": "m1", "context_length": 32_768},
            {"id": "m2", "meta": {"n_ctx": 8_192}},
            {"id": "m3", "max_model_len": 128_000},
            {"id": "m4", "architecture": {"context_window": 16_384}},
            {"id": "m5"},  # sin dato
        ]
    }

    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse())
    result = fetch_models("https://x/v1")
    assert result.context_for("m1") == 32_768
    assert result.context_for("m2") == 8_192
    assert result.context_for("m3") == 128_000
    assert result.context_for("m4") == 16_384
    assert result.context_for("m5") is None
    assert result.context_for("inexistente") is None


def test_discover_window_never_raises(monkeypatch) -> None:
    from satellite.context.compaction import discover_window

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("sin red")))
    assert discover_window(base_url="https://x/v1", model="m") is None
    assert discover_window(base_url="", model="m") is None


def test_window_cache_roundtrip(tmp_path: Path) -> None:
    from satellite.context.compaction import cached_window, remember_window, window_cache_path

    assert cached_window(tmp_path, "m") is None
    remember_window(tmp_path, "m", 65_536)
    assert window_cache_path(tmp_path).exists()
    assert cached_window(tmp_path, "m") == 65_536
    remember_window(tmp_path, "otro", 4_096)
    assert cached_window(tmp_path, "otro") == 4_096
    assert cached_window(tmp_path, "m") == 65_536


def test_chat_uses_the_window_declared_by_the_provider(tmp_path: Path, monkeypatch) -> None:
    """La ventana sale de la data del proveedor y se recuerda (sin red extra)."""
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        '{"llm": {"provider": "openai", "model": "modelo-del-proveedor", '
        '"base_url": "https://api.ejemplo/v1", "api_key": "k"}}',
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_discover(**kwargs):
        calls.append(kwargs)
        return 65_536, True

    monkeypatch.setattr(
        "satellite.context.compaction.discover_window_result", fake_discover
    )
    session = ChatSession(tmp_path, FakeOrchestrator())
    usage = session.context_usage("hola")
    assert usage.window_tokens == 65_536
    assert usage.window_source == "proveedor"
    assert calls and calls[0]["base_url"] == "https://api.ejemplo/v1"
    # Las claves que queden en el config del proyecto se ignoran.
    assert calls[0]["api_key"] == ""

    # segunda sesión: tira de la cache en disco, sin consultar la red
    monkeypatch.setattr(
        "satellite.context.compaction.discover_window_result",
        lambda **kwargs: pytest.fail("no debe volver a consultar la red"),
    )
    other = ChatSession(tmp_path, FakeOrchestrator())
    assert other.context_usage().window_tokens == 65_536


def test_chat_falls_back_when_the_provider_has_no_data(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        '{"llm": {"model": "claude-3-5-sonnet-latest", "base_url": "https://api.anthropic.com"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("satellite.context.compaction.discover_window", lambda **kwargs: None)
    session = ChatSession(tmp_path, FakeOrchestrator())
    usage = session.context_usage()
    assert usage.window_tokens == 200_000  # tabla de modelos conocidos
    assert usage.window_source == "modelo"


# ---------------------------------------------------------------- barra


def test_render_bar_shapes() -> None:
    from satellite.context.compaction import render_bar

    assert render_bar(0.0, 10) == "░" * 10
    assert render_bar(0.5, 10) == "█" * 5 + "░" * 5
    assert render_bar(1.0, 10) == "█" * 10
    assert render_bar(1.4, 10) == "█" * 10  # se acota
    assert render_bar(0.5, 8, unicode_ok=False) == "####----"


def test_human_tokens_and_style() -> None:
    from satellite.context.compaction import human_tokens, usage_style

    assert human_tokens(512) == "512"
    assert human_tokens(61_400) == "61.4k"
    assert human_tokens(1_200_000) == "1.2M"
    assert usage_style(0.2) == "ok"
    assert usage_style(0.75) == "aviso"
    assert usage_style(0.9) == "peligro"


def test_format_usage_line() -> None:
    from satellite.context.compaction import format_usage

    usage = ContextUsage(used_tokens=64_000, window_tokens=128_000, window_source="proveedor")
    line = format_usage(usage, unicode_ok=True)
    assert "contexto [" in line
    assert "50%" in line
    assert "64.0k/128.0k" in line
    assert "ventana: proveedor" in line
    plain = format_usage(usage, unicode_ok=False)
    assert "#" in plain and "█" not in plain


# ---------------------------------------------------------------- CLI


def test_cli_context_command_prints_the_bar(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from satellite.cli.app import app

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        '{"llm": {"model": "claude-3-5-sonnet-latest", "base_url": "https://api.anthropic.com"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("satellite.context.compaction.discover_window", lambda **kwargs: None)
    result = CliRunner().invoke(app, ["context", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "contexto [" in result.output
    assert "%" in result.output
    assert "autocompactado al 85%" in result.output


# ---------------------------------------------------------------- TUI


def test_status_bar_shows_the_context_bar() -> None:
    text = str(status_bar(SessionStatus(project_root="/tmp", agents=3, skills=0, context_percent=42)))
    assert "ctx" in text
    assert "42%" in text
    assert "█" in text or "░" in text
    empty = str(status_bar(SessionStatus(project_root="/tmp", agents=3, skills=0, context_percent=0)))
    assert "ctx" not in empty


def test_session_reports_context_without_conversation(tmp_path: Path, monkeypatch) -> None:
    from satellite.tui.session import Session

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    cfg = tmp_path / ".satellite" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        '{"name": "x", "model": "claude-3-5-sonnet-latest"}', encoding="utf-8"
    )
    session = Session(tmp_path, home=tmp_path / "home")
    usage = session.context_usage()
    assert usage.window_tokens == 200_000  # por tabla de modelo
    assert session.status().context_percent == 0


def test_palette_has_the_compact_command() -> None:
    from satellite.tui import palette

    assert "compact" in palette.command_names()
    assert palette.get("compact").usage == "/compact"
