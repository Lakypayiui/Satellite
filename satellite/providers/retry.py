"""Reintentos ante fallos transitorios del proveedor (conexión, timeout, 429, 5xx).

Un corte de red o un 503 momentáneo no debe tumbar un turno entero: ``RetryingProvider``
repite la llamada con espera exponencial y sólo propaga el error cuando se agotan los
intentos o el fallo no es transitorio (clave inválida, petición mal formada…).

Quien quiera enterarse de cada reintento (para enseñar «reconectando…») fija
``retry_listener`` en el contexto de la llamada; el proveedor no conoce la UI.
"""

from __future__ import annotations

import asyncio
import random
from contextvars import ContextVar
from typing import AsyncIterator, Awaitable, Callable

from satellite.core.messages import LLMRequest, LLMResponse, StreamChunk
from satellite.providers.base import LLMProvider

# 6 intentos con espera 2, 4, 8, 16 y 30 s: aguantan ≈1 minuto de corte (con 4 intentos
# y 1 s de base, ≈15 s, un corte de la evaluación v6 tumbó dos casos).
DEFAULT_ATTEMPTS = 6
DEFAULT_BASE_DELAY_S = 2.0
MAX_DELAY_S = 30.0
# Lo que el proveedor pide esperar (``Retry-After``) se respeta hasta este tope.
MAX_RETRY_AFTER_S = 60.0
TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
_TRANSIENT_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
        "TimeoutException",
        "NetworkError",
    }
)

RetryListener = Callable[[dict], None]
# Lo fija quien hace la llamada (p. ej. el agente) para publicar los reintentos.
retry_listener: ContextVar[RetryListener | None] = ContextVar("retry_listener", default=None)


def is_transient(exc: BaseException) -> bool:
    """True si el error (o alguna de sus causas) parece pasajero."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
            return True
        if type(current).__name__ in _TRANSIENT_NAMES:
            return True
        status = getattr(current, "status_code", None)
        if status is None:
            status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int) and status in TRANSIENT_STATUS:
            return True
        current = current.__cause__ or current.__context__
    return False


class RetryingProvider(LLMProvider):
    """Decorador que reintenta las llamadas que fallan por causas transitorias."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        attempts: int = DEFAULT_ATTEMPTS,
        base_delay_s: float = DEFAULT_BASE_DELAY_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.inner = inner
        self.attempts = max(1, int(attempts))
        self.base_delay_s = max(0.0, float(base_delay_s))
        self._sleep = sleep

    def __getattr__(self, name: str):
        # Nombre, modelo, config… del proveedor real: el decorador es transparente.
        return getattr(self.inner, name)

    @property
    def reports_usage(self) -> bool:
        return bool(getattr(self.inner, "reports_usage", True))

    @property
    def automatic_cache(self) -> bool:
        return bool(getattr(self.inner, "automatic_cache", False))

    def cache_strategy(self):
        return self.inner.cache_strategy()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        attempt = 1
        while True:
            try:
                return await self.inner.complete(request)
            except Exception as exc:  # noqa: BLE001 — se decide si es transitorio
                if attempt >= self.attempts or not is_transient(exc):
                    raise
                await self._wait(attempt, exc)
                attempt += 1

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Reintenta el stream mientras no haya cedido texto.

        Si se corta a mitad, el texto ya publicado no se puede retirar: se pide la
        respuesta completa sin streaming (con sus propios reintentos) y se cede lo que
        falta de texto (cuando la respuesta continúa lo ya emitido) y la respuesta final.
        """
        attempt = 1
        while True:
            streamed = ""
            try:
                async for chunk in self.inner.stream(request):
                    if chunk.text:
                        streamed += chunk.text
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001 — se decide si es transitorio
                if attempt >= self.attempts or not is_transient(exc):
                    raise
                await self._wait(attempt, exc)
                attempt += 1
                if streamed:
                    response = await self._complete_from(attempt, request)
                    content = response.content or ""
                    if content.startswith(streamed) and len(content) > len(streamed):
                        # Quien une los deltas obtiene la respuesta entera, como siempre.
                        yield StreamChunk(text=content[len(streamed):])
                    yield StreamChunk(response=response)
                    return

    async def _complete_from(self, attempt: int, request: LLMRequest) -> LLMResponse:
        while True:
            try:
                return await self.inner.complete(request)
            except Exception as exc:  # noqa: BLE001 — se decide si es transitorio
                if attempt >= self.attempts or not is_transient(exc):
                    raise
                await self._wait(attempt, exc)
                attempt += 1

    async def _wait(self, attempt: int, exc: BaseException) -> None:
        delay = retry_after_s(exc)
        if delay is None:
            delay = min(MAX_DELAY_S, self.base_delay_s * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25) if delay else 0.0  # nosec B311
        listener = retry_listener.get()
        if listener is not None:
            try:
                listener(
                    {
                        "kind": "provider",
                        "attempt": attempt + 1,
                        "max": self.attempts,
                        "wait_s": round(delay, 1),
                        "error": _short(exc),
                    }
                )
            except Exception:  # noqa: BLE001 — avisar nunca rompe la llamada
                pass
        await self._sleep(delay)


def with_retries(provider: LLMProvider, *, attempts: int = DEFAULT_ATTEMPTS,
                 base_delay_s: float = DEFAULT_BASE_DELAY_S) -> LLMProvider:
    """Envuelve ``provider`` con reintentos (idempotente; ``attempts<=1`` lo deja igual)."""
    if isinstance(provider, RetryingProvider) or attempts <= 1:
        return provider
    return RetryingProvider(provider, attempts=attempts, base_delay_s=base_delay_s)


def retry_after_s(exc: BaseException) -> float | None:
    """Segundos que pide esperar el proveedor (cabecera ``Retry-After``), con tope.

    Acepta segundos o una fecha HTTP; ``None`` si no hay cabecera válida.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        headers = getattr(getattr(current, "response", None), "headers", None)
        value = None
        if headers is not None:
            try:
                value = headers.get("retry-after") or headers.get("Retry-After")
            except Exception:  # noqa: BLE001 — cabeceras raras: se ignoran
                value = None
        if value:
            seconds = _parse_retry_after(str(value))
            if seconds is not None:
                return min(MAX_RETRY_AFTER_S, max(0.0, seconds))
        current = current.__cause__ or current.__context__
    return None


def _parse_retry_after(value: str) -> float | None:
    try:
        return float(value.strip())
    except ValueError:
        pass
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).total_seconds()


def _short(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 160 else text[:157] + "…"


__all__ = [
    "RetryingProvider",
    "is_transient",
    "retry_after_s",
    "retry_listener",
    "with_retries",
]
