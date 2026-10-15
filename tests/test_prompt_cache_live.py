"""Smoke **opt-in** contra proveedores reales ([AJ-10]) — cuesta tokens.

Qué comprueba: que la **segunda llamada idéntica** lee de cache (el contrato real
del proveedor, que no se puede simular). Es el único test que sale a la red.

Cuándo corre: **sólo** si existe ``ANTHROPIC_API_KEY`` (o ``OPENAI_API_KEY``) y no
se ha desactivado con ``SATELLITE_LIVE_TESTS=0``. En un entorno sin claves se
salta, así que la suite normal no gasta ni red ni dinero.

Variables opcionales:
- ``SATELLITE_ANTHROPIC_MODEL`` (por defecto ``claude-sonnet-5-5``; los ids
  vigentes se listan con ``python -m satellite.cli provider models``)
- ``SATELLITE_OPENAI_MODEL`` (por defecto ``gpt-4o-mini``)
- ``SATELLITE_LIVE_TESTS=0`` para desactivarlo aunque haya claves.
"""

import asyncio
import os
import time

import pytest

from satellite.core.messages import LLMRequest, Message, Role
from satellite.providers.factory import ProviderConfig

LIVE = os.getenv("SATELLITE_LIVE_TESTS", "1") != "0"
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("SATELLITE_ANTHROPIC_MODEL", "claude-sonnet-5-5")
OPENAI_MODEL = os.getenv("SATELLITE_OPENAI_MODEL", "gpt-4o-mini")

# Prefijo bien por encima del mínimo de cache (1024 tokens): ~4.500 estimados.
FILLER = "El veloz murciélago hindú comía feliz cardillo y kiwi. " * 300


def run(coro):
    return asyncio.run(coro)


def make_request() -> LLMRequest:
    """Petición con un prefijo estable largo y una cola corta idéntica."""
    return LLMRequest(
        messages=(
            Message(
                role=Role.SYSTEM,
                content="Eres un asistente conciso.\n\nContexto estable:\n" + FILLER,
            ),
            Message(role=Role.USER, content="Responde únicamente con la palabra: ok"),
        ),
        max_tokens=16,
        temperature=0.0,
    )


def cached_tokens(response) -> int:
    """Tokens servidos desde cache (0 si el proveedor no lo reporta)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return int(usage.cache_read_input_tokens or 0)


@pytest.mark.skipif(
    not LIVE or not ANTHROPIC_KEY, reason="requiere ANTHROPIC_API_KEY (smoke opt-in)"
)
def test_anthropic_second_identical_call_reads_cache() -> None:
    pytest.importorskip("anthropic")
    from satellite.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model=ANTHROPIC_MODEL, api_key=ANTHROPIC_KEY)
    )
    request = make_request()
    first = run(provider.complete(request))
    assert first.content is not None, f"la primera llamada falló: {first}"

    second = run(provider.complete(request))
    read = cached_tokens(second)
    assert read > 0, (
        "la segunda llamada idéntica no leyó de cache "
        f"(usage={second.usage}, plan={provider.last_plan})"
    )
    assert second.usage is not None and second.usage.has_cache_data


@pytest.mark.skipif(
    not LIVE or not OPENAI_KEY, reason="requiere OPENAI_API_KEY (smoke opt-in)"
)
def test_openai_second_identical_call_reads_cache() -> None:
    pytest.importorskip("openai")
    from satellite.providers.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(
        ProviderConfig(name="openai", model=OPENAI_MODEL, api_key=OPENAI_KEY)
    )
    request = make_request()
    first = run(provider.complete(request))
    assert first.content is not None, f"la primera llamada falló: {first}"

    read = 0
    for _ in range(3):  # el cacheado automático de OpenAI puede tardar un momento
        time.sleep(2)
        second = run(provider.complete(request))
        read = cached_tokens(second)
        if read > 0:
            break
    assert read > 0, (
        "la segunda llamada idéntica no leyó de cache (prompt_tokens_details)"
    )
