"""Tests de OpenAICompatProvider con cliente falso (sin red)."""

import asyncio

from satellite.core.messages import LLMRequest, Message, Role, Usage
from satellite.core.tool_models import ToolSchema
from satellite.providers.factory import ProviderConfig
from satellite.providers.openai_compat import OpenAICompatProvider


def run(coro):
    return asyncio.run(coro)


class FakeOpenAIResponse:
    def __init__(self, content="", tool_calls=None, finish_reason="stop", usage=None):
        self.choices = [FakeChoice(content, tool_calls, finish_reason)]
        self.usage = usage


class FakeChoice:
    def __init__(self, content="", tool_calls=None, finish_reason="stop"):
        self.message = FakeMessage(content, tool_calls)
        self.finish_reason = finish_reason


class FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeFunctionCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, tc_id, name, arguments):
        self.id = tc_id
        self.function = FakeFunctionCall(name, arguments)


class FakeUsage:
    def __init__(self, prompt=None, comp=None, total=None):
        self.prompt_tokens = prompt
        self.completion_tokens = comp
        self.total_tokens = total


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []
        self.response = None

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def make_provider() -> tuple[OpenAICompatProvider, FakeOpenAIClient]:
    client = FakeOpenAIClient()
    cfg = ProviderConfig(name="openai", model="gpt-4o-mini", api_key="test")
    return OpenAICompatProvider(cfg, client=client), client


def make_request(tools=False, **overrides) -> LLMRequest:
    messages = (Message(role=Role.SYSTEM, content="sys"), Message(role=Role.USER, content="hola"))
    kwargs = dict(messages=messages)
    if tools:
        kwargs["tools"] = (
            ToolSchema(name="read_file", description="lee", parameters={"type": "object"}),
        )
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def test_reasoning_models_use_max_completion_tokens_without_temperature() -> None:
    """GPT-5 y las series `o` rechazan `max_tokens` y un `temperature` no estándar."""
    for model in ("gpt-5-mini", "o3-mini", "o1-preview"):
        client = FakeOpenAIClient()
        client.response = FakeOpenAIResponse(content="ok")
        provider = OpenAICompatProvider(
            ProviderConfig(name="openai", model=model, api_key="test"), client=client
        )
        run(provider.complete(make_request(max_tokens=128, temperature=0.7)))
        kwargs = client.calls[-1]
        assert kwargs["max_completion_tokens"] == 128, model
        assert "max_tokens" not in kwargs, model
        assert "temperature" not in kwargs, model


def test_classic_models_keep_temperature_and_max_tokens() -> None:
    provider, client = make_provider()  # gpt-4o-mini
    client.response = FakeOpenAIResponse(content="ok")
    run(provider.complete(make_request(max_tokens=64, temperature=0.7)))
    kwargs = client.calls[-1]
    assert kwargs["max_tokens"] == 64
    assert kwargs["temperature"] == 0.7
    assert "max_completion_tokens" not in kwargs


def test_model_capabilities_can_be_overridden_from_config() -> None:
    """Una familia nueva se arregla por config, sin tocar el código."""
    cfg = ProviderConfig(
        name="openai",
        model="mi-modelo-nuevo",
        api_key="test",
        model_overrides={"mi-modelo-nuevo": {"reasoning": True}},
    )
    client = FakeOpenAIClient()
    client.response = FakeOpenAIResponse(content="ok")
    run(OpenAICompatProvider(cfg, client=client).complete(make_request(max_tokens=10, temperature=0.5)))
    assert client.calls[-1]["max_completion_tokens"] == 10
    assert "temperature" not in client.calls[-1]

    # y al revés: un modelo de la tabla al que no le aplica la sospecha
    cfg2 = ProviderConfig(
        name="openai",
        model="o3-viejo",
        api_key="test",
        model_overrides={"o3-viejo": {"reasoning": False, "accepts_temperature": True}},
    )
    client2 = FakeOpenAIClient()
    client2.response = FakeOpenAIResponse(content="ok")
    run(
        OpenAICompatProvider(cfg2, client=client2).complete(
            make_request(max_tokens=10, temperature=0.5)
        )
    )
    assert client2.calls[-1]["max_tokens"] == 10
    assert client2.calls[-1]["temperature"] == 0.5


def test_translates_request() -> None:
    provider, client = make_provider()
    client.response = FakeOpenAIResponse(content="respuesta", usage=FakeUsage(1, 2, 3))
    resp = run(provider.complete(make_request(tools=True, temperature=0.5, max_tokens=100)))
    call = client.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["messages"][0] == {"role": "system", "content": "sys"}
    assert call["messages"][1] == {"role": "user", "content": "hola"}
    assert call["tools"][0]["type"] == "function"
    assert call["temperature"] == 0.5
    assert call["max_tokens"] == 100
    assert resp.content == "respuesta"
    assert resp.usage.total_tokens == 3
    assert resp.stop_reason == "stop"


def test_empty_tools_are_not_sent() -> None:
    """An empty tools tuple (synthesis without tools) must omit the field.

    Several compatible providers reject `tools: []` with HTTP 400, which made a
    plain synthesis call look like a model failure.
    """
    provider, client = make_provider()
    client.response = FakeOpenAIResponse(content="ok")
    run(provider.complete(make_request(tools=())))
    assert "tools" not in client.calls[-1]


def test_assistant_with_tool_calls_normalized() -> None:
    provider, client = make_provider()
    client.response = FakeOpenAIResponse(
        content=None,
        tool_calls=[FakeToolCall("c1", "read_file", '{"path":"a.txt"}')],
        finish_reason="tool_calls",
    )
    resp = run(provider.complete(make_request()))
    assert resp.tool_calls is not None
    assert resp.tool_calls[0].id == "c1"
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "a.txt"}
    assert resp.stop_reason == "tool_calls"


def test_tool_message_translation() -> None:
    provider, client = make_provider()
    client.response = FakeOpenAIResponse(content="ok")
    req = LLMRequest(
        messages=(
            Message(role=Role.ASSISTANT, content="", tool_calls=()),
            Message(role=Role.TOOL, content="resultado", tool_call_id="c1"),
        )
    )
    run(provider.complete(req))
    tool_msg = client.calls[0]["messages"][1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == "resultado"
