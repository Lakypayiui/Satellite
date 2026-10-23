"""Reintentos del proveedor ante fallos transitorios (``satellite/providers/retry.py``)."""

import asyncio

import pytest

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse, Message, Role, StreamChunk
from satellite.providers.base import LLMProvider
from satellite.providers.retry import (
    RetryingProvider,
    is_transient,
    retry_listener,
    with_retries,
)


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def wrapped(cause: BaseException) -> ProviderError:
    try:
        raise ProviderError("error del proveedor") from cause
    except ProviderError as exc:
        return exc


class Flaky(LLMProvider):
    """Falla ``failures`` veces con ``error`` y luego responde."""

    def __init__(self, failures: int, error: BaseException, *, partial: bool = False) -> None:
        self.failures = failures
        self.error = error
        self.partial = partial
        self.calls = 0
        self.stream_calls = 0
        self.name = "flaky"

    async def complete(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return LLMResponse(content="hola")

    async def stream(self, request):
        self.stream_calls += 1
        if self.partial:
            yield StreamChunk(text="ho")
            raise self.error
        if self.stream_calls <= self.failures:
            raise self.error
        yield StreamChunk(text="hola")
        yield StreamChunk(response=LLMResponse(content="hola"))


def request() -> LLMRequest:
    return LLMRequest(messages=(Message(role=Role.USER, content="hi"),))


async def no_sleep(_delay: float) -> None:
    return None


def test_transient_classification() -> None:
    assert is_transient(wrapped(ConnectionResetError("reset")))
    assert is_transient(wrapped(StatusError(503)))
    assert is_transient(wrapped(StatusError(429)))
    assert is_transient(asyncio.TimeoutError())
    assert not is_transient(wrapped(StatusError(401)))
    assert not is_transient(wrapped(ValueError("bad request")))


def test_complete_retries_transient_and_reports() -> None:
    inner = Flaky(2, wrapped(ConnectionError("down")))
    provider = RetryingProvider(inner, attempts=4, sleep=no_sleep)
    seen: list[dict] = []
    token = retry_listener.set(seen.append)
    try:
        response = asyncio.run(provider.complete(request()))
    finally:
        retry_listener.reset(token)
    assert response.content == "hola"
    assert inner.calls == 3
    assert [item["attempt"] for item in seen] == [2, 3]
    assert all(item["kind"] == "provider" and item["max"] == 4 for item in seen)


def test_complete_gives_up_after_attempts() -> None:
    inner = Flaky(10, wrapped(ConnectionError("down")))
    provider = RetryingProvider(inner, attempts=3, sleep=no_sleep)
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete(request()))
    assert inner.calls == 3


def test_non_transient_error_is_not_retried() -> None:
    inner = Flaky(1, wrapped(StatusError(401)))
    provider = RetryingProvider(inner, attempts=4, sleep=no_sleep)
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete(request()))
    assert inner.calls == 1


async def collect(provider: LLMProvider) -> list[StreamChunk]:
    return [chunk async for chunk in provider.stream(request())]


def test_stream_retries_before_first_text() -> None:
    inner = Flaky(1, wrapped(ConnectionError("down")))
    chunks = asyncio.run(collect(RetryingProvider(inner, attempts=3, sleep=no_sleep)))
    assert inner.stream_calls == 2
    assert chunks[-1].response is not None and chunks[-1].response.content == "hola"


def test_stream_cut_midway_finishes_with_complete() -> None:
    inner = Flaky(0, wrapped(ConnectionError("cut")), partial=True)
    chunks = asyncio.run(collect(RetryingProvider(inner, attempts=3, sleep=no_sleep)))
    # Lo ya emitido («ho») se completa con lo que falta: unidos, los deltas son la
    # respuesta entera, igual que sin corte.
    assert "".join(chunk.text for chunk in chunks if chunk.text) == "hola"
    assert chunks[-1].response is not None and chunks[-1].response.content == "hola"
    assert inner.stream_calls == 1 and inner.calls == 1


def test_stream_cut_midway_with_a_different_answer_adds_no_text() -> None:
    class Different(Flaky):
        async def complete(self, request):
            self.calls += 1
            return LLMResponse(content="adiós")

    inner = Different(0, wrapped(ConnectionError("cut")), partial=True)
    chunks = asyncio.run(collect(RetryingProvider(inner, attempts=3, sleep=no_sleep)))
    # No continúa lo emitido: no se pega texto que no encaje; manda la respuesta final.
    assert [chunk.text for chunk in chunks if chunk.text] == ["ho"]
    assert chunks[-1].response is not None and chunks[-1].response.content == "adiós"


def test_with_retries_is_idempotent_and_transparent() -> None:
    inner = Flaky(0, ConnectionError())
    once = with_retries(inner)
    assert with_retries(once) is once
    assert with_retries(inner, attempts=1) is inner
    assert once.name == "flaky"


def test_retry_config_from_settings() -> None:
    from satellite.cli.runtime_builder import _retry_config

    assert _retry_config({"retry": {"attempts": 6, "base_delay_s": 0.5}}) == {
        "attempts": 6,
        "base_delay_s": 0.5,
    }
    assert _retry_config({"retry": {"attempts": 0, "base_delay_s": "x"}}) == {}
    assert _retry_config(None) == {}


# --- Fase D del plan de correcciones: aguantar cortes de minutos ----------------------


def test_the_defaults_survive_about_a_minute_of_outage() -> None:
    waits: list[float] = []

    async def record(delay: float) -> None:
        waits.append(delay)

    inner = Flaky(5, ConnectionError("caído"))
    response = asyncio.run(RetryingProvider(inner, sleep=record).complete(request()))

    assert response.content == "hola"
    assert inner.calls == 6
    assert len(waits) == 5
    assert 55 <= sum(waits) <= 80  # ≈1 minuto de margen, con jitter
    assert max(waits) <= 30 * 1.25


class RateLimited(Exception):
    def __init__(self, retry_after: str) -> None:
        super().__init__("HTTP 429")
        self.status_code = 429
        self.response = type("R", (), {"status_code": 429, "headers": {"retry-after": retry_after}})()


def test_retry_after_is_honored() -> None:
    waits: list[float] = []

    async def record(delay: float) -> None:
        waits.append(delay)

    inner = Flaky(1, RateLimited("7"))
    asyncio.run(RetryingProvider(inner, sleep=record).complete(request()))

    assert waits == [7.0]


def test_a_huge_retry_after_is_capped() -> None:
    waits: list[float] = []

    async def record(delay: float) -> None:
        waits.append(delay)

    inner = Flaky(1, RateLimited("3600"))
    asyncio.run(RetryingProvider(inner, sleep=record).complete(request()))

    assert waits == [60.0]
