"""Caché al día: diagnóstico de la API, ancla rodante, modo auto y pre-calentado."""

import asyncio

from satellite.core.messages import LLMRequest, Message, Role
from satellite.observability.call_log import CallRecord
from satellite.observability.drift import (
    DIAGNOSIS_INCONCLUSIVE,
    KIND_CHANGED,
    KIND_MODEL_OR_TOOLS_CHANGED,
    diagnosis_kind,
)
from satellite.providers.anthropic_provider import AnthropicProvider
from satellite.providers.caching import (
    ANCHOR_OFFSET,
    AnthropicCacheStrategy,
    CacheLimits,
    limits_for,
    parse_cache_config,
)
from satellite.providers.factory import ProviderConfig
from satellite.providers.fake import FakeLLM
from satellite.providers.metered import MeteredProvider


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- diagnóstico


def test_diagnosis_maps_the_api_reasons() -> None:
    sent = {"previous_message_id": "msg_1", "present": True}
    assert (
        diagnosis_kind({**sent, "cache_miss_reason": {"type": "model_changed"}})
        == KIND_MODEL_OR_TOOLS_CHANGED
    )
    assert (
        diagnosis_kind({**sent, "cache_miss_reason": {"type": "tools_changed"}})
        == KIND_MODEL_OR_TOOLS_CHANGED
    )
    assert (
        diagnosis_kind({**sent, "cache_miss_reason": {"type": "system_changed"}})
        == KIND_CHANGED
    )
    # el catálogo va en un bloque USER: un cambio suyo llega como messages_changed
    assert (
        diagnosis_kind({**sent, "cache_miss_reason": {"type": "messages_changed"}})
        == KIND_CHANGED
    )


def test_diagnosis_is_inconclusive_when_there_is_no_verdict() -> None:
    # sin diagnóstico (no se pidió)
    assert diagnosis_kind(None) == DIAGNOSIS_INCONCLUSIVE
    # primer turno del prefijo: no hay nada que comparar
    assert (
        diagnosis_kind({"previous_message_id": None, "present": True, "cache_miss_reason": None})
        == DIAGNOSIS_INCONCLUSIVE
    )
    # la comparación seguía en curso
    assert (
        diagnosis_kind({"previous_message_id": "msg_1", "present": True, "cache_miss_reason": None})
        == DIAGNOSIS_INCONCLUSIVE
    )
    # huella no encontrada / no disponible
    for reason in ("previous_message_not_found", "unavailable"):
        verdict = diagnosis_kind(
            {"previous_message_id": "msg_1", "present": True, "cache_miss_reason": {"type": reason}}
        )
        assert verdict == DIAGNOSIS_INCONCLUSIVE, reason


def test_diagnosis_none_with_a_previous_id_means_no_divergence() -> None:
    """`null` con un id real = comparó y no hay divergencia (no «sin datos»)."""
    assert (
        diagnosis_kind({"previous_message_id": "msg_1", "present": False, "cache_miss_reason": None})
        == ""
    )


def test_call_record_roundtrips_breakpoints_and_diagnosis() -> None:
    record = CallRecord(
        breakpoints=("system", "catalog", "anchor", "tail"),
        message_id="msg_1",
        cache_miss_reason="tools_changed",
        diagnosis=KIND_MODEL_OR_TOOLS_CHANGED,
    )
    restored = CallRecord.from_dict(record.to_dict())
    assert restored.breakpoints == ("system", "catalog", "anchor", "tail")
    assert restored.message_id == "msg_1"
    assert restored.cache_miss_reason == "tools_changed"
    assert restored.diagnosis == KIND_MODEL_OR_TOOLS_CHANGED
    # tolerante con registros antiguos que guardaban números
    legacy = CallRecord.from_dict({"breakpoints": [1, 2]})
    assert legacy.breakpoints == ("1", "2")


# ---------------------------------------------------------------- plan


def test_plan_marks_an_anchor_only_in_long_conversations() -> None:
    strategy = AnthropicCacheStrategy()
    limits = limits_for("anthropic", "claude-sonnet-5-5", overrides={"min_prefix_tokens": 10})
    short = strategy.plan(
        prefix_tokens=100, has_system=True, has_catalog=True, has_tail=True, limits=limits, blocks=5
    )
    assert short.mark_anchor is False
    assert short.breakpoints == ("system", "catalog", "tail")

    long = strategy.plan(
        prefix_tokens=100, has_system=True, has_catalog=True, has_tail=True, limits=limits, blocks=30
    )
    assert long.mark_anchor is True
    assert long.breakpoints == ("system", "catalog", "anchor", "tail")
    assert long.count == 4  # los cuatro slots disponibles


def test_auto_mode_never_marks_blocks_nor_the_anchor() -> None:
    strategy = AnthropicCacheStrategy()
    limits = CacheLimits(min_prefix_tokens=10)
    plan = strategy.plan(
        prefix_tokens=100,
        has_system=True,
        has_catalog=True,
        has_tail=True,
        limits=limits,
        blocks=30,
        mode="auto",
    )
    assert plan.count == 0
    assert plan.mark_anchor is False
    assert "auto" in plan.skipped_reason


def test_parse_cache_config_reads_the_new_options() -> None:
    parsed = parse_cache_config(
        {"ttl": "1h", "mode": "auto", "diagnostics": True, "prewarm": 1, "modex": "x"}
    )
    assert parsed["ttl"] == "1h"
    assert parsed["mode"] == "auto"
    assert parsed["diagnostics"] is True
    assert parsed["prewarm"] is True
    assert parse_cache_config({"mode": "inventado"}) == {}  # se ignora lo inválido


# ---------------------------------------------------------------- adaptador


class FakeBetaClient:
    """Cliente falso con ruta beta: captura kwargs y devuelve una respuesta."""

    def __init__(self, response=None) -> None:
        self.calls: list = []
        self.response = response

    @property
    def messages(self):
        return self

    @property
    def beta(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeBlock:
    def __init__(self, text="ok", block_type="text"):
        self.type = block_type
        self.text = text
        self.id = ""
        self.name = ""
        self.input = {}


class FakeReason:
    def __init__(self, reason_type):
        self.type = reason_type
        self.cache_missed_input_tokens = 12


class FakeDiagnostics:
    def __init__(self, reason_type=None):
        self.cache_miss_reason = None if reason_type is None else FakeReason(reason_type)


class FakeResponse:
    def __init__(self, response_id="msg_1", diagnostics=None):
        self.id = response_id
        self.diagnostics = diagnostics
        self.content = [FakeBlock()]
        self.usage = None
        self.stop_reason = "end_turn"


def make_request() -> LLMRequest:
    return LLMRequest(
        messages=(
            Message(role=Role.SYSTEM, content="sys"),
            Message(role=Role.USER, content="catálogo"),
            Message(role=Role.USER, content="hola"),
        ),
        stable_user_count=1,
    )


def test_diagnostics_are_opt_in_and_threaded_by_prefix() -> None:
    client = FakeBetaClient(FakeResponse("msg_1", FakeDiagnostics("tools_changed")))
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-x", api_key="k"),
        client=client,
        cache_options={"diagnostics": True},
    )

    first = run(provider.complete(make_request()))
    # primer turno del prefijo: se opta por el diagnóstico con `null`
    assert client.calls[0]["diagnostics"] == {"previous_message_id": None}
    assert first.response_id == "msg_1"
    assert first.cache_diagnostics["cache_miss_reason"]["type"] == "tools_changed"

    run(provider.complete(make_request()))
    # segundo turno: se manda el id anterior
    assert client.calls[1]["diagnostics"] == {"previous_message_id": "msg_1"}


def test_diagnostics_are_off_by_default_and_never_break_the_call() -> None:
    client = FakeBetaClient(FakeResponse())
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-x", api_key="k"), client=client
    )
    response = run(provider.complete(make_request()))
    assert "diagnostics" not in client.calls[0]
    assert response.cache_diagnostics is None

    class NoBeta:
        def __init__(self) -> None:
            self.calls: list = []

        @property
        def messages(self):
            return self

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return FakeResponse()

    plain = NoBeta()
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-x", api_key="k"),
        client=plain,
        cache_options={"diagnostics": True},
    )
    run(provider.complete(make_request()))  # no revienta sin ruta beta
    assert "diagnostics" not in plain.calls[0]


def test_auto_mode_sends_a_top_level_cache_control() -> None:
    client = FakeBetaClient(FakeResponse())
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-x", api_key="k"),
        client=client,
        limits=CacheLimits(min_prefix_tokens=0),
        cache_options={"mode": "auto"},
    )
    run(provider.complete(make_request()))
    kwargs = client.calls[0]
    assert kwargs["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    # sin marcadores por bloque
    assert "cache_control" not in kwargs["messages"][-1]["content"][0]


def test_anchor_is_placed_before_the_tail() -> None:
    client = FakeBetaClient(FakeResponse())
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-x", api_key="k"),
        client=client,
        limits=CacheLimits(min_prefix_tokens=0),
    )
    messages = (
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content="catálogo"),
    ) + tuple(Message(role=Role.USER, content=f"m{index}") for index in range(20))
    request = LLMRequest(messages=messages, stable_user_count=1)
    run(provider.complete(request))
    plan = provider.last_plan
    assert plan is not None and plan.mark_anchor is True
    sent = client.calls[0]["messages"]
    assert "cache_control" in sent[-1]["content"][0]
    assert "cache_control" in sent[-1 - ANCHOR_OFFSET]["content"][0]


# ---------------------------------------------------------------- prewarm


def test_prewarm_is_off_by_default_and_refreshes_only_once() -> None:
    inner = FakeLLM(script=[{"content": "ok"}, {"content": "ok"}])
    metered = MeteredProvider(inner, model="m", prewarm=False)
    run(metered.complete(make_request()))
    assert inner.requests == [inner.requests[0]]  # sin llamada extra
    assert len(inner.requests) == 1

    inner2 = FakeLLM(script=[{"content": "ok"}, {"content": "ok"}])
    warming = MeteredProvider(inner2, model="m", prewarm=True)
    run(warming.complete(make_request()))
    assert len(inner2.requests) == 2  # prewarm + llamada real
    prewarm = inner2.requests[0]
    assert prewarm.max_tokens == 0
    assert prewarm.messages[-1].content == "warmup"
    assert prewarm.stable_user_count == len(prewarm.messages) - 1
    assert warming.last_prewarmed is True

    # el prefijo ya está calentado: no se repite
    run(warming.complete(make_request()))
    assert len(inner2.requests) == 3
