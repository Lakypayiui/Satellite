"""Tests de AnthropicProvider con cliente falso (sin red)."""

import asyncio

from satellite.core.messages import LLMRequest, Message, Role
from satellite.core.tool_models import ToolSchema
from satellite.providers.anthropic_provider import AnthropicProvider
from satellite.providers.factory import ProviderConfig


def run(coro):
    return asyncio.run(coro)


class FakeBlock:
    def __init__(self, block_type, text="", tool_id="", name="", tool_input=None):
        self.type = block_type
        self.text = text
        self.id = tool_id
        self.name = name
        self.input = tool_input or {}


class FakeUsage:
    def __init__(self, inp=0, out=0):
        self.input_tokens = inp
        self.output_tokens = out


class FakeAnthropicResponse:
    def __init__(self, content, stop_reason="end_turn", usage=None):
        self.content = content
        self.stop_reason = stop_reason
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


def make_provider() -> tuple[AnthropicProvider, FakeAnthropicClient]:
    client = FakeAnthropicClient()
    cfg = ProviderConfig(name="anthropic", model="claude-3-5-sonnet", api_key="test")
    return AnthropicProvider(cfg, client=client), client


def make_request(tools=False, **overrides) -> LLMRequest:
    messages = (Message(role=Role.SYSTEM, content="sys"), Message(role=Role.USER, content="hola"))
    kwargs = dict(messages=messages)
    if tools:
        kwargs["tools"] = (
            ToolSchema(name="read_file", description="lee", parameters={"type": "object"}),
        )
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def test_system_extracted_and_messages_translated() -> None:
    provider, client = make_provider()
    client.response = FakeAnthropicResponse(
        content=[FakeBlock("text", "respuesta")],
        usage=FakeUsage(5, 7),
    )
    resp = run(provider.complete(make_request(tools=True)))
    call = client.calls[0]
    assert call["model"] == "claude-3-5-sonnet"
    # el system va como bloques (permite cache_control); sin marcador si el
    # prefijo es pequeño
    assert call["system"] == [{"type": "text", "text": "sys"}]
    assert call["messages"] == [{"role": "user", "content": "hola"}]
    assert call["tools"][0]["name"] == "read_file"
    assert call["tools"][0]["input_schema"] == {"type": "object"}
    assert resp.content == "respuesta"
    assert resp.usage.prompt_tokens == 5
    assert resp.usage.completion_tokens == 7


def test_tool_use_normalized() -> None:
    provider, client = make_provider()
    client.response = FakeAnthropicResponse(
        content=[
            FakeBlock("text", "voy a leer"),
            FakeBlock("tool_use", tool_id="tu1", name="read_file", tool_input={"path": "a"}),
        ],
        stop_reason="tool_use",
        usage=FakeUsage(1, 2),
    )
    resp = run(provider.complete(make_request()))
    assert resp.content == "voy a leer"
    assert resp.tool_calls is not None
    assert resp.tool_calls[0].id == "tu1"
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "a"}
    assert resp.stop_reason == "tool_calls"


def test_max_tokens_is_always_sent() -> None:
    """Anthropic exige ``max_tokens``: petición → config → defecto (4096)."""
    from satellite.providers.anthropic_provider import DEFAULT_MAX_OUTPUT_TOKENS

    provider, client = make_provider()
    client.response = FakeAnthropicResponse(content=[FakeBlock("text", "ok")])

    run(provider.complete(make_request()))
    assert client.calls[-1]["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS

    run(provider.complete(make_request(max_tokens=64)))
    assert client.calls[-1]["max_tokens"] == 64

    configured = ProviderConfig(
        name="anthropic", model="claude-3-5-sonnet", api_key="test", max_tokens=256
    )
    other = AnthropicProvider(configured, client=client)
    run(other.complete(make_request()))
    assert client.calls[-1]["max_tokens"] == 256

    # ninguna llamada sale sin límite
    assert all("max_tokens" in call for call in client.calls)


def test_tool_result_translation() -> None:
    provider, client = make_provider()
    client.response = FakeAnthropicResponse(content=[FakeBlock("text", "ok")])
    req = LLMRequest(
        messages=(
            Message(role=Role.ASSISTANT, content="", tool_calls=()),
            Message(role=Role.TOOL, content="resultado", tool_call_id="tu1"),
        )
    )
    run(provider.complete(req))
    msg = client.calls[0]["messages"][1]
    assert msg["role"] == "user"
    assert msg["content"][0]["type"] == "tool_result"
    assert msg["content"][0]["tool_use_id"] == "tu1"
    assert msg["content"][0]["content"] == "resultado"
