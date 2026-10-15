"""Interfaz de proveedores LLM."""

from satellite.providers.base import LLMProvider
from satellite.providers.fake import FakeLLM
from satellite.providers.factory import ProviderConfig, ProviderFactory
from satellite.providers.openai_compat import OpenAICompatProvider
from satellite.providers.anthropic_provider import AnthropicProvider

__all__ = [
    "LLMProvider",
    "FakeLLM",
    "ProviderConfig",
    "ProviderFactory",
    "OpenAICompatProvider",
    "AnthropicProvider",
]
