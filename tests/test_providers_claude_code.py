"""Claude Code account provider: ciclo de vida del subproceso (sin CLI real)."""

import asyncio

import pytest

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, Message, Role
from satellite.providers.claude_code_plan import ClaudeCodePlanProvider
from satellite.providers.factory import ProviderConfig


def run(coro):
    return asyncio.run(coro)


class _Process:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.waited = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        self.returncode = -9
        return self.returncode


class _SuccessProcess(_Process):
    async def communicate(self, payload=None):
        self.returncode = 0
        stdout = b'{"structured_output": {"content": "hola", "tool_calls": []}}'
        return stdout, b""


class _BlockingProcess(_Process):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def communicate(self, payload=None):
        self.started.set()
        await asyncio.Event().wait()


def _provider(timeout_s: float = 240) -> ClaudeCodePlanProvider:
    return ClaudeCodePlanProvider(
        ProviderConfig(
            name="anthropic",
            model="sonnet",
            auth_type="claude_code_account",
        ),
        timeout_s=timeout_s,
    )


def _request() -> LLMRequest:
    return LLMRequest(messages=(Message(role=Role.USER, content="Hola"),))


def _fake_process(monkeypatch, process) -> None:
    monkeypatch.setattr(
        "satellite.providers.claude_code_plan.shutil.which",
        lambda _name: "/usr/bin/claude",
    )

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(
        "satellite.providers.claude_code_plan.asyncio.create_subprocess_exec",
        create,
    )


def test_timeout_kills_and_waits_the_process(monkeypatch) -> None:
    process = _BlockingProcess()
    _fake_process(monkeypatch, process)

    with pytest.raises(ProviderError, match="tardó demasiado"):
        run(_provider(timeout_s=0.05).complete(_request()))

    assert process.killed is True
    assert process.waited is True


def test_cancellation_kills_and_waits_the_process(monkeypatch) -> None:
    process = _BlockingProcess()
    _fake_process(monkeypatch, process)
    provider = _provider()

    async def scenario() -> None:
        task = asyncio.create_task(provider.complete(_request()))
        await asyncio.wait_for(process.started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())

    assert process.killed is True
    assert process.waited is True


def test_success_does_not_kill_the_process(monkeypatch) -> None:
    process = _SuccessProcess()
    _fake_process(monkeypatch, process)

    response = run(_provider().complete(_request()))

    assert response.content == "hola"
    assert process.killed is False
    assert process.waited is False
