"""Métricas por llamada (F4): registro, deriva, precios e informe."""

import asyncio
import json
from pathlib import Path

import pytest

from satellite.core.messages import CallContext, LLMRequest, Message, Role
from satellite.kernel.policy import load_settings
from satellite.metrics.cache_report import build_cache_report
from satellite.metrics.pricing import ModelPrice, PriceBook
from satellite.observability.call_log import (
    CACHED,
    CALLS_FILE,
    MAX_FIELD_CHARS,
    MAX_LINE_BYTES,
    NOT_CACHED,
    UNKNOWN,
    WRITTEN,
    CallRecord,
    CallRecorder,
    cache_status,
)
from satellite.observability.drift import PrefixDriftDetector
from satellite.providers.caching import NoCacheStrategy
from satellite.providers.fake import FakeLLM
from satellite.providers.metered import MeteredProvider

DRIFT = "drift"
CATALOG_CHANGED = "catalog_changed"
MODEL_OR_TOOLS = "model_or_tools_changed"


def run(coro):
    return asyncio.run(coro)


def make_request(
    *,
    system: str = "SYS",
    catalog: str = "CAT",
    role: str = "agent",
    task_id: str = "t1",
    agent_id: str = "a1",
    spec_fp: str = "spec",
    catalog_fp: str = "cat",
    stable: int = 1,
    max_tokens: int | None = None,
) -> LLMRequest:
    messages = [Message(role=Role.SYSTEM, content=system)]
    if catalog:
        messages.append(Message(role=Role.USER, content=catalog))
    messages.append(Message(role=Role.USER, content="Tarea: x"))
    return LLMRequest(
        messages=tuple(messages),
        stable_user_count=stable,
        max_tokens=max_tokens,
        call=CallContext(
            task_id=task_id,
            agent_id=agent_id,
            role=role,
            spec_fp=spec_fp,
            catalog_fp=catalog_fp,
        ),
    )


# ---------------------------------------------------------------- estado de cache


@pytest.mark.parametrize(
    "creation,read,expected",
    [
        (None, None, UNKNOWN),
        (0, 0, NOT_CACHED),
        (100, 0, WRITTEN),
        (0, 50, CACHED),
        (100, 50, CACHED),
    ],
)
def test_cache_status_distinguishes_unknown_from_zero(creation, read, expected) -> None:
    assert cache_status(creation, read) == expected


# ---------------------------------------------------------------- registro


def test_call_record_roundtrip_and_has_no_content() -> None:
    record = CallRecord(call_id="c1", model="m", input_tokens=10, cache_read=5)
    assert CallRecord.from_dict(record.to_dict()) == record
    blob = json.dumps(record.to_dict())
    for forbidden in ("prompt", "content", "api_key", "messages", "response", "system"):
        assert forbidden not in blob


def test_recorder_appends_one_line_per_call(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    recorder.record(CallRecord(call_id="a", task_id="t"))
    recorder.record(CallRecord(call_id="b", task_id="t"))
    lines = (tmp_path / CALLS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [r.call_id for r in recorder.load()] == ["a", "b"]
    assert recorder.count() == 2
    # sin call_id se genera uno
    recorder.record(CallRecord())
    assert recorder.load()[-1].call_id.startswith("call_")


def test_recorder_skips_corrupt_lines(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    recorder.record(CallRecord(call_id="a"))
    path = tmp_path / CALLS_FILE
    path.write_text(path.read_text(encoding="utf-8") + "{no soy json\n", encoding="utf-8")
    assert [r.call_id for r in recorder.load()] == ["a"]


def test_recorder_rotates_and_drops_oldest(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path, max_bytes=200, keep=2)
    for index in range(20):
        recorder.record(CallRecord(call_id=f"c{index}", task_id="x" * 40))
    assert (tmp_path / f"{CALLS_FILE}.1").exists()
    assert not (tmp_path / f"{CALLS_FILE}.3").exists()
    assert recorder.load()[-1].call_id == "c19"


def test_recorder_trims_long_fields(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    recorder.record(
        CallRecord(model="m" * 20000, provider="p" * 20000, agent_id="a" * 20000)
    )
    line = (tmp_path / CALLS_FILE).read_text(encoding="utf-8").splitlines()[0]
    assert len(line.encode("utf-8")) <= MAX_LINE_BYTES
    assert len(recorder.load()[0].model) <= MAX_FIELD_CHARS


def test_recorder_survives_unwritable_dir(tmp_path: Path) -> None:
    target = tmp_path / "fichero"  # no es un directorio
    target.write_text("x", encoding="utf-8")
    assert CallRecorder(target).record(CallRecord()) is None


# ---------------------------------------------------------------- deriva


def test_drift_detector_classifies_changes() -> None:
    detector = PrefixDriftDetector()
    base = dict(task_id="t", agent_id="a", model="m", spec_fp="s", catalog_fp="c", tools_fp="tl")
    assert detector.classify(prefix_hash="h1", **base) == ""
    assert detector.classify(prefix_hash="h1", **base) == ""
    # mismo todo menos el hash -> deriva real
    assert detector.classify(prefix_hash="h2", **base) == DRIFT
    # cambios legítimos: catálogo / modelo+tools
    assert detector.classify(prefix_hash="h3", **{**base, "catalog_fp": "c2"}) == CATALOG_CHANGED
    assert (
        detector.classify(prefix_hash="h4", **{**base, "catalog_fp": "c2", "tools_fp": "tl2"})
        == MODEL_OR_TOOLS
    )


def test_drift_detector_is_per_task_and_agent() -> None:
    detector = PrefixDriftDetector()
    detector.classify(task_id="t1", agent_id="a", model="m", prefix_hash="h1")
    assert detector.classify(task_id="t2", agent_id="a", model="m", prefix_hash="h9") == ""
    assert detector.snapshot_for("t1", "a").prefix_hash == "h1"


# ---------------------------------------------------------------- precios


def test_price_book_empty_means_unknown() -> None:
    book = PriceBook()
    assert book.is_empty is True
    assert book.cost("m", input_tokens=10, output_tokens=1) is None
    assert book.hypothetical_cost("m", input_tokens=10, output_tokens=1) is None


def test_price_book_cost_with_cache() -> None:
    book = PriceBook(
        {"gpt": ModelPrice(input=10.0, output=30.0, cache_write=12.5, cache_read=1.0)}
    )
    assert book.cost("gpt", input_tokens=1_000_000, output_tokens=0) == 10.0
    cost = book.cost(
        "gpt", input_tokens=1_000_000, output_tokens=0, cache_creation=400_000, cache_read=200_000
    )
    expected = (400_000 * 10.0 + 400_000 * 12.5 + 200_000 * 1.0) / 1_000_000
    assert cost == pytest.approx(expected)
    hypothetical = book.hypothetical_cost("gpt", input_tokens=1_000_000, output_tokens=0)
    assert hypothetical == 10.0
    assert cost < hypothetical


def test_price_book_from_config_and_prefix_match() -> None:
    book = PriceBook.from_config(
        {"llm": {"pricing": {"claude-3-5-sonnet": {"input": 3, "output": 15}}}}
    )
    assert len(book) == 1
    assert book.price_for("claude-3-5-sonnet-20241022") is not None
    assert book.price_for("otro-modelo") is None
    assert PriceBook.from_config({}).is_empty
    assert PriceBook.from_config(None).is_empty


# ---------------------------------------------------------------- informe


def test_build_cache_report_marks_unknown() -> None:
    records = [
        CallRecord(role="orchestrator", agent_id="orchestrator", model="m", input_tokens=100, output_tokens=10, cache_creation=0, cache_read=100),
        CallRecord(role="orchestrator", agent_id="orchestrator", model="m", input_tokens=200, output_tokens=20, cache_creation=200, cache_read=0),
        CallRecord(role="agent", agent_id="w", model="m", input_tokens=50, output_tokens=5),
    ]
    report = build_cache_report(records)
    assert report.calls == 3
    assert report.input_tokens == 350
    assert report.unknown_calls == 1
    assert report.cache_read == 100
    assert report.hit_rate == round(100 / 350, 4)
    assert report.statuses == {CACHED: 1, WRITTEN: 1, UNKNOWN: 1}
    assert report.real_cost is None
    assert "unknown" in report.format()
    assert report.by_role["orchestrator"]["calls"] == 2
    assert report.by_agent["w"]["calls"] == 1


def test_build_cache_report_empty_is_explicit() -> None:
    report = build_cache_report([])
    assert report.calls == 0
    assert report.hit_rate is None
    assert "0 llamadas" in report.format()


def test_build_cache_report_costs_and_savings() -> None:
    book = PriceBook({"m": ModelPrice(input=10.0, output=30.0, cache_write=12.5, cache_read=1.0)})
    records = [
        CallRecord(
            model="m",
            input_tokens=1_000_000,
            output_tokens=0,
            cache_creation=0,
            cache_read=500_000,
        )
    ]
    report = build_cache_report(records, book)
    assert report.real_cost == pytest.approx((500_000 * 10.0 + 500_000 * 1.0) / 1_000_000)
    assert report.hypothetical_cost == 10.0
    assert report.savings is not None and report.savings > 0
    assert report.savings_pct == pytest.approx(report.savings / 10.0)
    assert report.priced_calls == 1
    assert "coste real" in report.format()


def test_build_cache_report_counts_drift() -> None:
    report = build_cache_report([CallRecord(kind=DRIFT), CallRecord(kind="")])
    assert report.drift_events == 1


def test_real_tokens_per_call_uses_effective_input_plus_output() -> None:
    records = [
        CallRecord(input_tokens=1000, output_tokens=100, cache_read=800, cache_creation=0),
        CallRecord(input_tokens=1000, output_tokens=100, cache_read=0, cache_creation=1000),
    ]
    report = build_cache_report(records)
    # (1000-800+100 + 1000-0+100) / 2 = 700  (lo leído de cache no va a precio pleno)
    assert report.real_tokens_per_call == 700.0


# ---------------------------------------------------------------- provider medido


def test_metered_provider_records_the_call(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    inner = FakeLLM(
        script=[
            {
                "content": "ok",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 5,
                    "total_tokens": 105,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 0,
                },
            }
        ]
    )
    provider = MeteredProvider(inner, recorder, provider_name="fake", model="m")
    response = run(provider.complete(make_request()))
    assert response.content == "ok"
    records = recorder.load()
    assert len(records) == 1
    record = records[0]
    assert record.cache_status == CACHED
    assert record.input_tokens == 100
    assert record.output_tokens == 5
    assert record.role == "agent"
    assert record.provider == "fake"
    assert len(record.prefix_hash) == 64
    assert record.prefix_tokens_estimate > 0
    assert provider.last_record is not None


def test_metered_provider_without_recorder_still_works() -> None:
    inner = FakeLLM()
    provider = MeteredProvider(inner, None)
    assert run(provider.complete(make_request())).content == "ok"
    assert provider.last_record is None


def test_metered_provider_injects_max_tokens_by_role() -> None:
    inner = FakeLLM()
    provider = MeteredProvider(inner, None, max_tokens_by_role={"creator": 512, "*": 64})
    run(provider.complete(make_request(role="creator")))
    run(provider.complete(make_request(role="agent")))
    run(provider.complete(make_request(role="creator", max_tokens=99)))
    assert inner.requests[0].max_tokens == 512
    assert inner.requests[1].max_tokens == 64
    assert inner.requests[2].max_tokens == 99  # no pisa un límite explícito


def test_metered_provider_delegates_capabilities() -> None:
    inner = FakeLLM()
    provider = MeteredProvider(inner, None)
    assert provider.reports_usage == inner.reports_usage
    assert provider.automatic_cache == inner.automatic_cache
    assert isinstance(provider.cache_strategy(), NoCacheStrategy)


def test_metered_provider_detects_drift_on_prefix_change(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    inner = FakeLLM(script=[{"content": "a"}, {"content": "b"}, {"content": "c"}])
    provider = MeteredProvider(inner, recorder, model="m")
    run(provider.complete(make_request(system="S1")))
    run(provider.complete(make_request(system="S1")))
    run(provider.complete(make_request(system="S2")))
    kinds = [record.kind for record in recorder.load()]
    assert kinds == ["", "", DRIFT]


# ---------------------------------------------------------------- ahorro [AJ-8]


def test_skill_savings_use_real_cost_with_cache() -> None:
    """[AJ-8] el ahorro por skill usa el coste REAL con cache, no el hipotético."""
    from satellite.metrics.stats import build_report

    manifests = [
        {
            "id": "mi_skill",
            "stats": {
                "uses": 10,
                "successes": 10,
                "first_resolution_tokens": 1000,
                "creation_tokens": 5000,
            },
        }
    ]
    hypothetical = build_report(manifests, call_cost_tokens=1000)
    real = build_report(manifests, call_cost_tokens=300)
    assert hypothetical.total_savings == -5000  # sin ahorro: coste = línea base
    assert real.total_savings == 10 * (1000 - 300) - 5000
    assert real.skills[0].break_even_uses == pytest.approx(5000 / 700, abs=0.01)


def test_report_carries_the_cache_summary() -> None:
    from satellite.metrics.cache_report import build_cache_report
    from satellite.metrics.stats import build_report

    cache = build_cache_report([CallRecord(input_tokens=100, cache_read=50, cache_creation=0)])
    report = build_report([], cache=cache.to_dict())
    assert report.to_dict()["cache"]["hit_rate"] == 0.5
    assert build_report([]).to_dict()["cache"] == {}


def test_runtime_installs_the_metered_provider(tmp_path: Path, monkeypatch) -> None:
    """El runtime real envuelve el proveedor para medir (cableado de F4)."""
    from satellite.cli.runtime_builder import build_runtime

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    cfg_dir = tmp_path / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "fake-model"}), encoding="utf-8"
    )
    orchestrator, _bus, _store, _registry = build_runtime(tmp_path)
    provider = orchestrator.services.provider
    # El Safe Proxy (modo interno) envuelve por fuera; la medición sigue instalada dentro.
    from satellite.safe_proxy.interceptor import SanitizingProvider

    if isinstance(provider, SanitizingProvider):
        provider = provider.inner
    assert isinstance(provider, MeteredProvider)
    assert provider.model == "fake-model"
    assert provider.recorder is not None
    assert provider.recorder.path.parent == tmp_path / ".satellite" / "observability"


def test_runtime_meters_a_real_call(tmp_path: Path, monkeypatch) -> None:
    """Una llamada a través del runtime queda registrada en calls.jsonl."""
    from satellite.cli.runtime_builder import build_runtime

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    cfg_dir = tmp_path / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "fake-model"}), encoding="utf-8"
    )
    orchestrator, _bus, _store, _registry = build_runtime(tmp_path)
    provider = orchestrator.services.provider
    run(
        provider.complete(
            LLMRequest(
                messages=(
                    Message(role=Role.SYSTEM, content="SYS"),
                    Message(role=Role.USER, content="hola"),
                ),
                call=CallContext(task_id="t", role="agent", agent_id="orchestrator"),
            )
        )
    )
    records = CallRecorder(tmp_path / ".satellite" / "observability").load()
    assert len(records) == 1
    assert records[0].task_id == "t"
    assert records[0].provider == "fake"


# ---------------------------------------------------------------- max_tokens


def test_settings_max_tokens_by_role_and_scalar(tmp_path: Path) -> None:
    by_role = tmp_path / "por_rol.json"
    by_role.write_text(
        json.dumps({"llm": {"max_tokens": {"creator": 512, "*": 128}}}), encoding="utf-8"
    )
    settings = load_settings(by_role)
    assert settings.max_tokens_for("creator") == 512
    assert settings.max_tokens_for("otro") == 128

    scalar = tmp_path / "escalar.json"
    scalar.write_text(json.dumps({"llm": {"max_tokens": 256}}), encoding="utf-8")
    assert load_settings(scalar).max_tokens_for("lo-que-sea") == 256

    assert load_settings(tmp_path / "nope.json").max_tokens_for("x") is None
