"""Tests de ProviderFactory."""

import pytest

from satellite.core.errors import ProviderError
from satellite.providers.anthropic_provider import AnthropicProvider
from satellite.providers.factory import ProviderConfig, ProviderFactory
from satellite.providers.fake import FakeLLM
from satellite.providers.openai_compat import OpenAICompatProvider


def test_fake_creates_fake() -> None:
    provider = ProviderFactory.create(ProviderConfig(name="fake", model="x"))
    assert isinstance(provider, FakeLLM)


def test_openai_creates_openai() -> None:
    provider = ProviderFactory.create(
        ProviderConfig(name="openai", model="gpt-4o-mini", api_key="k")
    )
    assert isinstance(provider, OpenAICompatProvider)


def test_deepseek_ollama_llama_cpp_gemini() -> None:
    for name in ("deepseek", "ollama", "llama_cpp", "gemini"):
        provider = ProviderFactory.create(ProviderConfig(name=name, model="m"))
        assert isinstance(provider, OpenAICompatProvider), name


def test_anthropic_creates_anthropic() -> None:
    provider = ProviderFactory.create(ProviderConfig(name="anthropic", model="claude"))
    assert isinstance(provider, AnthropicProvider)


def test_base_url_creates_openai_compat() -> None:
    provider = ProviderFactory.create(
        ProviderConfig(name="local", model="m", base_url="http://localhost:11434/v1")
    )
    assert isinstance(provider, OpenAICompatProvider)


def test_unknown_raises_provider_error() -> None:
    with pytest.raises(ProviderError):
        ProviderFactory.create(ProviderConfig(name="desconocido", model="m"))
