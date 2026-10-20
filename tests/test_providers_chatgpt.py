"""ChatGPT account provider: parse streamed Responses events (no network)."""

import asyncio
import json

import pytest

from satellite.core.messages import LLMRequest, Message, Role
from satellite.providers.chatgpt_plan import ChatGPTPlanProvider
from satellite.providers.factory import ProviderConfig


def run(coro):
    return asyncio.run(coro)


class _TokenReply:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"access_token": "access-token"}


class _Stream:
    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b""


class _Client:
    lines: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url, headers=None):
        return _TokenReply()

    def stream(self, method, url, headers=None, json=None):
        return _Stream(self.lines)


def _provider() -> ChatGPTPlanProvider:
    return ChatGPTPlanProvider(
        ProviderConfig(name="chatgpt", model="gpt-6-luna", auth_type="chatgpt_account")
    )


def _request() -> LLMRequest:
    return LLMRequest(
        messages=(
            Message(role=Role.SYSTEM, content="sys"),
            Message(role=Role.USER, content="Hola"),
        )
    )


def _event(kind: str, **fields) -> str:
    return "data: " + json.dumps({"type": kind, **fields})


@pytest.fixture(autouse=True)
def _broker(monkeypatch):
    monkeypatch.setenv("SATELLITE_ACCOUNT_BROKER_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("SATELLITE_ACCOUNT_BROKER_SECRET", "secret")
    monkeypatch.setattr(
        "satellite.providers.chatgpt_plan.httpx.AsyncClient", _Client
    )


def test_payload_omits_empty_tools() -> None:
    assert "tools" not in _provider()._payload(_request())


def test_completed_with_empty_output_uses_streamed_deltas() -> None:
    """The account endpoint sends text in deltas and `output: []` when completed."""
    _Client.lines = [
        _event("response.output_text.delta", delta="¡Hola"),
        _event("response.output_text.delta", delta="!"),
        _event(
            "response.completed",
            response={"output": [], "usage": {"input_tokens": 4, "output_tokens": 2}},
        ),
    ]

    response = run(_provider().complete(_request()))

    assert response.content == "¡Hola!"
    assert response.usage is not None and response.usage.completion_tokens == 2


def test_output_text_done_is_used_when_completed_output_is_empty() -> None:
    _Client.lines = [
        _event("response.output_text.done", text="Respuesta completa."),
        _event("response.completed", response={"output": []}),
    ]

    assert run(_provider().complete(_request())).content == "Respuesta completa."


def test_streamed_function_call_is_returned() -> None:
    _Client.lines = [
        _event(
            "response.function_call_arguments.done",
            call_id="c1",
            name="read_file",
            arguments='{"path": "a.txt"}',
        ),
        _event("response.completed", response={"output": []}),
    ]

    response = run(_provider().complete(_request()))

    assert response.tool_calls is not None
    assert response.tool_calls[0].id == "c1"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "a.txt"}
    assert response.stop_reason == "tool_calls"


def test_completed_output_still_wins_when_present() -> None:
    message = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "desde output"}],
    }
    _Client.lines = [
        _event("response.output_text.done", text="desde stream"),
        _event("response.completed", response={"output": [message]}),
    ]

    assert run(_provider().complete(_request())).content == "desde output"
