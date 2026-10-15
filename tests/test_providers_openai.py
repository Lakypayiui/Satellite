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
