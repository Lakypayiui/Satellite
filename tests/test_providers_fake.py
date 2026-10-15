"""Tests de FakeLLM."""

import asyncio

from satellite.core.messages import LLMRequest, Message, Role
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def make_request() -> LLMRequest:
    return LLMRequest(messages=(Message(role=Role.USER, content="hola"),))


def test_script_sequential() -> None:
    fake = FakeLLM(
        script=[
            {"content": "uno"},
            {"tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "a"}}]},
        ]
    )
    r1 = run(fake.complete(make_request()))
    assert r1.content == "uno"
    r2 = run(fake.complete(make_request()))
    assert r2.tool_calls is not None
    assert r2.tool_calls[0].name == "read_file"
    assert r2.tool_calls[0].arguments == {"path": "a"}


def test_script_exhausted_uses_default() -> None:
    fake = FakeLLM(script=[{"content": "solo"}], default_content="default")
    r1 = run(fake.complete(make_request()))
    assert r1.content == "solo"
    r2 = run(fake.complete(make_request()))
    assert r2.content == "default"


def test_registers_requests() -> None:
    fake = FakeLLM()
    run(fake.complete(make_request()))
    run(fake.complete(make_request()))
    assert len(fake.requests) == 2
    assert fake.requests[0].messages[0].content == "hola"


def test_error_step_raises() -> None:
    fake = FakeLLM(script=[{"error": "boom"}])
    import pytest

    from satellite.core.errors import ProviderError

    with pytest.raises(ProviderError, match="boom"):
        run(fake.complete(make_request()))
