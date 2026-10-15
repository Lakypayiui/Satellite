"""Tests de prompt caching en el adaptador (estrategia, breakpoints y usage)."""

import asyncio

import pytest

from satellite.core.errors import ContractError
from satellite.core.messages import LLMRequest, Message, Role, Usage
from satellite.core.tool_models import ToolSchema
from satellite.providers.anthropic_provider import AnthropicProvider
from satellite.providers.caching import (
    TTL_1H,
    TTL_5M,
    AnthropicCacheStrategy,
    CacheLimits,
    CachePlan,
    NoCacheStrategy,
    TtlLedger,
    limits_for,
    parse_cache_config,
    resolve_ttl,
    strategy_for,
)
from satellite.providers.fake import FakeLLM
from satellite.providers.factory import ProviderConfig
from satellite.providers.openai_compat import OpenAICompatProvider
from satellite.providers.prefix import (
    estimate_prefix_tokens,
    prefix_hash,
    prefix_payload,
    tools_canonical,
    tools_fingerprint,
)

BIG_TEXT = "contexto estable. " * 500  # ~9000 caracteres → por encima del mínimo


def run(coro):
    return asyncio.run(coro)


# --- prefix (determinismo) ---

def test_tools_sorted_by_name() -> None:
    schemas = [
        ToolSchema(name="write_file", description="w", parameters={}),
        ToolSchema(name="read_file", description="r", parameters={}),
    ]
    assert [t["name"] for t in tools_canonical(schemas)] == ["read_file", "write_file"]


def test_prefix_is_order_independent() -> None:
    a = [ToolSchema(name="b", description="", parameters={}), ToolSchema(name="a", description="", parameters={})]
    b = list(reversed(a))
    assert tools_fingerprint(a) == tools_fingerprint(b)
    assert prefix_hash(a, "sys", "cat") == prefix_hash(b, "sys", "cat")


def test_prefix_hash_changes_with_content() -> None:
    assert prefix_hash([], "sys", "cat") != prefix_hash([], "sys", "otro")
    assert prefix_hash([], "sys") != prefix_hash([], "sys2")


def test_prefix_payload_has_fixed_order() -> None:
    payload = prefix_payload([], "S", "C")
    assert payload.index("TOOLS:") < payload.index("SYSTEM:") < payload.index("CATALOG:")


def test_estimate_prefix_tokens_has_margin() -> None:
    assert estimate_prefix_tokens("") > 0
    assert estimate_prefix_tokens(BIG_TEXT) > len(BIG_TEXT) // 4


# --- estrategia ---

def test_no_cache_strategy_never_marks() -> None:
    plan = NoCacheStrategy().plan(
        prefix_tokens=10_000, has_system=True, has_catalog=True, has_tail=True,
        limits=CacheLimits(ttl=TTL_5M),
    )
    assert plan.count == 0
    assert plan.skipped_reason


def test_anthropic_plan_marks_all_segments() -> None:
    strategy = AnthropicCacheStrategy()
    plan = strategy.plan(
        prefix_tokens=5_000, has_system=True, has_catalog=True, has_tail=True,
        limits=CacheLimits(),
    )
    assert plan.breakpoints == ("system", "catalog", "tail")
    assert plan.skipped_reason == ""


def test_anthropic_plan_skips_small_prefix() -> None:
    strategy = AnthropicCacheStrategy()
    plan = strategy.plan(
        prefix_tokens=10, has_system=True, has_catalog=True, has_tail=False,
        limits=CacheLimits(min_prefix_tokens=1024),
    )
    assert plan.count == 0
    assert "por debajo del mínimo" in plan.skipped_reason


def test_anthropic_plan_respects_max_breakpoints() -> None:
    strategy = AnthropicCacheStrategy()
    plan = strategy.plan(
        prefix_tokens=5_000, has_system=True, has_catalog=True, has_tail=True,
        limits=CacheLimits(max_breakpoints=2),
    )
    # prioridad: del final hacia atrás
    assert plan.breakpoints == ("catalog", "tail")


def test_marker_shape() -> None:
    assert AnthropicCacheStrategy().marker(TTL_1H) == {"type": "ephemeral", "ttl": "1h"}


def test_cache_limits_validation() -> None:
    with pytest.raises(ContractError):
        CacheLimits(ttl="2h")
    with pytest.raises(ContractError):
        CacheLimits(max_breakpoints=0)


def test_cache_plan_dict() -> None:
    plan = CachePlan(ttl=TTL_5M, mark_system=True, mark_tail=True)
    assert plan.to_dict()["breakpoints"] == ["system", "tail"]
    assert plan.count == 2


# --- TTL ---

def test_resolve_ttl_never_shortens() -> None:
    assert resolve_ttl(TTL_1H, None) == (TTL_1H, False)
    assert resolve_ttl(TTL_5M, TTL_1H) == (TTL_1H, True)
    assert resolve_ttl(TTL_1H, TTL_5M) == (TTL_1H, False)


def test_ttl_ledger_remembers() -> None:
    ledger = TtlLedger()
    assert ledger.effective("h", TTL_1H) == (TTL_1H, False)
    assert ledger.effective("h", TTL_5M) == (TTL_1H, True)
    ledger.forget("h")
    assert ledger.effective("h", TTL_5M) == (TTL_5M, False)


# --- límites por modelo ---

def test_limits_for_anthropic_haiku() -> None:
    assert limits_for("anthropic", "claude-3-5-haiku").min_prefix_tokens == 2048
    assert limits_for("anthropic", "claude-3-5-sonnet").min_prefix_tokens == 1024
    assert limits_for("openai", "gpt-4o").min_prefix_tokens == 0


def test_minimums_follow_the_current_documentation() -> None:
    """Líneas 5.x → 512; Opus 4.5/4.6 y Haiku 4.5 → 4.096; sin dato → 1.024."""
    cases = {
        "claude-sonnet-5-5": 512,
        "claude-opus-5-5": 512,
        "claude-haiku-5-5": 512,
        "claude-sonnet-5": 1024,
        "claude-sonnet-4-5": 1024,
        "claude-opus-4-8": 1024,
        "claude-opus-4-7": 2048,
        "claude-opus-4-6": 4096,
        "claude-opus-4-5": 4096,
        "claude-haiku-4-5": 4096,
        "claude-3-opus": 1024,  # sin dato publicado: nos quedamos cortos
        "modelo-desconocido": 1024,
    }
    for model, expected in cases.items():
        assert limits_for("anthropic", model).min_prefix_tokens == expected, model
    # el par que rompe una clave por "línea": 4.5 y 5.5 miden distinto
    assert (
        limits_for("anthropic", "claude-haiku-4-5").min_prefix_tokens
        != limits_for("anthropic", "claude-haiku-5-5").min_prefix_tokens
    )
    # y el mínimo concreto se puede sobrescribir desde config
    overridden = limits_for("anthropic", "claude-haiku-5-5", overrides={"min_prefix_tokens": 999})
    assert overridden.min_prefix_tokens == 999


def test_limits_for_overrides() -> None:
    limits = limits_for("anthropic", "sonnet", overrides={"ttl": TTL_1H, "max_breakpoints": 2})
    assert limits.ttl == TTL_1H
    assert limits.max_breakpoints == 2


def test_strategy_for_and_config() -> None:
    assert strategy_for("anthropic").provider_name == "anthropic"
    assert strategy_for("openai").provider_name == "none"
    assert parse_cache_config({"ttl": "1h", "max_breakpoints": "3"}) == {
        "ttl": "1h",
        "max_breakpoints": 3,
    }
    assert parse_cache_config({"ttl": "9h"}) == {}


# --- aplicación en el provider ---

class FakeBlock:
    def __init__(self, block_type, text=""):
        self.type = block_type
        self.text = text


class FakeUsageWithCache:
    def __init__(self, inp=0, out=0, creation=None, read=None):
        self.input_tokens = inp
        self.output_tokens = out
        if creation is not None:
            self.cache_creation_input_tokens = creation
        if read is not None:
            self.cache_read_input_tokens = read


class FakeAnthropicResponse:
    def __init__(self, content, usage=None):
        self.content = content
        self.stop_reason = "end_turn"
        self.usage = usage


class FakeAnthropicClient:
    def __init__(self):
        self.calls = []
        self.response = None

    @property
    def messages(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def make_provider(**kwargs) -> tuple[AnthropicProvider, FakeAnthropicClient]:
    client = FakeAnthropicClient()
    cfg = ProviderConfig(name="anthropic", model="claude-3-5-sonnet", api_key="t")
    return AnthropicProvider(cfg, client=client, **kwargs), client


def big_request(**overrides) -> LLMRequest:
    messages = (
        Message(role=Role.SYSTEM, content=BIG_TEXT),
        Message(role=Role.USER, content="catálogo " + BIG_TEXT),
        Message(role=Role.USER, content="tarea concreta"),
        Message(role=Role.ASSISTANT, content="voy"),
    )
    kwargs = dict(
        messages=messages,
        tools=(ToolSchema(name="read_file", description="lee", parameters={}),),
        stable_user_count=1,
    )
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def test_provider_marks_breakpoints() -> None:
    provider, client = make_provider()
    client.response = FakeAnthropicResponse([FakeBlock("text", "ok")])
    run(provider.complete(big_request()))
    call = client.calls[0]
    marker = {"type": "ephemeral", "ttl": TTL_5M}
    assert call["system"][-1]["cache_control"] == marker
    assert call["messages"][0]["content"][-1]["cache_control"] == marker
    assert call["messages"][-1]["content"][-1]["cache_control"] == marker
    assert provider.last_plan is not None
    assert provider.last_plan.breakpoints == ("system", "catalog", "tail")


def test_provider_skips_small_prefix() -> None:
    provider, client = make_provider()
    client.response = FakeAnthropicResponse([FakeBlock("text", "ok")])
    request = LLMRequest(
        messages=(Message(role=Role.SYSTEM, content="sys"), Message(role=Role.USER, content="hola"))
    )
    run(provider.complete(request))
    call = client.calls[0]
    assert call["system"] == [{"type": "text", "text": "sys"}]
    assert "cache_control" not in call["tools"][-1] if "tools" in call else True
    assert provider.last_plan is not None
    assert provider.last_plan.skipped_reason


def test_provider_usage_normalises_anthropic() -> None:
    provider, client = make_provider()
    client.response = FakeAnthropicResponse(
        [FakeBlock("text", "ok")], usage=FakeUsageWithCache(100, 20, creation=300, read=700)
    )
    resp = run(provider.complete(big_request()))
    # Anthropic: input_tokens excluye lo cacheado → el input total es 1100
    assert resp.usage.prompt_tokens == 1100
    assert resp.usage.cache_creation_input_tokens == 300
    assert resp.usage.cache_read_input_tokens == 700
    assert resp.usage.effective_input_tokens == 400


def test_provider_usage_without_cache_fields_is_unknown() -> None:
    provider, client = make_provider()
    client.response = FakeAnthropicResponse([FakeBlock("text", "ok")], usage=FakeUsageWithCache(5, 7))
    resp = run(provider.complete(big_request()))
    assert resp.usage.cache_creation_input_tokens is None
    assert resp.usage.cache_read_input_tokens is None
    assert resp.usage.has_cache_data is False


def test_provider_ttl_degradation() -> None:
    provider, client = make_provider()
    provider._ttl_ledger.effective("x", TTL_1H)
    client.response = FakeAnthropicResponse([FakeBlock("text", "ok")])
    run(provider.complete(big_request()))
    digest = prefix_hash(
        big_request().tools, BIG_TEXT, "catálogo " + BIG_TEXT
    )
    provider._ttl_ledger.effective(digest, TTL_1H)
    client.response = FakeAnthropicResponse([FakeBlock("text", "ok")])
    run(provider.complete(big_request(max_tokens=None)))
    assert client.calls[-1]["system"][-1]["cache_control"]["ttl"] == TTL_1H


# --- OpenAI ---

class FakeUsage:
    def __init__(self, prompt=None, comp=None, total=None, cached=None):
        self.prompt_tokens = prompt
        self.completion_tokens = comp
        self.total_tokens = total
        if cached is not None:
            self.prompt_tokens_details = type("D", (), {"cached_tokens": cached})()


class FakeOpenAIResponse:
    def __init__(self, usage=None):
        message = type("M", (), {"content": "ok", "tool_calls": None})()
        choice = type("C", (), {"message": message, "finish_reason": "stop"})()
        self.choices = [choice]
        self.usage = usage


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeOpenAIResponse(FakeUsage(100, 20, 120, cached=80))


def test_openai_normalises_cached_tokens() -> None:
    client = FakeOpenAIClient()
    provider = OpenAICompatProvider(
        ProviderConfig(name="openai", model="gpt-4o", api_key="t"), client=client
    )
    resp = run(provider.complete(big_request()))
    assert provider.automatic_cache is True
    # OpenAI: prompt_tokens ya es el total; cached_tokens es subconjunto
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.cache_read_input_tokens == 80
    assert resp.usage.effective_input_tokens == 20


# --- FakeLLM con usage scripteado ---

def test_fake_llm_reports_scripted_usage() -> None:
    fake = FakeLLM(
        script=[
            {"content": "uno", "usage": {"prompt_tokens": 1000, "cache_creation_input_tokens": 1000}},
            {"content": "dos", "usage": {"prompt_tokens": 1000, "cache_read_input_tokens": 950}},
        ]
    )
    request = LLMRequest(messages=(Message(role=Role.USER, content="hola"),))
    first = run(fake.complete(request))
    second = run(fake.complete(request))
    assert first.usage.cache_creation_input_tokens == 1000
    assert second.usage.cache_read_input_tokens == 950
    assert second.usage.has_cache_data is True
    assert fake.reports_usage is True


def test_usage_roundtrip_with_cache() -> None:
    usage = Usage(
        prompt_tokens=10,
        completion_tokens=2,
        total_tokens=12,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=4,
    )
    restored = Usage.from_dict(usage.to_dict())
    assert restored == usage
    assert restored.cache_creation_input_tokens is None
