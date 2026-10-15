"""Factory de proveedores LLM."""

from __future__ import annotations

from dataclasses import dataclass, field

from satellite.core.errors import ProviderError
from satellite.providers.anthropic_provider import AnthropicProvider
from satellite.providers.base import LLMProvider
from satellite.providers.fake import FakeLLM
from satellite.providers.openai_compat import OpenAICompatProvider

_OPENAI_COMPAT_NAMES = {
    "openai",
    "deepseek",
    "ollama",
    "llama_cpp",
    "gemini",
}


@dataclass(frozen=True)
class ProviderConfig:
    """Configuración de un proveedor LLM."""

    name: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    # ``llm.models.<id>``: capacidades por modelo (p. ej. familias de razonamiento).
    # Fuera de ``eq``/``hash`` para que la config siga siendo hashable.
    model_overrides: dict = field(default_factory=dict, compare=False)


class ProviderFactory:
    """Crea el LLMProvider adecuado según el nombre de la config."""

    @staticmethod
    def create(cfg: ProviderConfig) -> LLMProvider:
        """Crea el provider correspondiente; ProviderError si es desconocido."""
        name = (cfg.name or "").lower()
        if name == "fake":
            return FakeLLM()
        if name == "anthropic":
            return AnthropicProvider(cfg)
        if name in _OPENAI_COMPAT_NAMES or cfg.base_url:
            return OpenAICompatProvider(cfg)
        raise ProviderError(
            f"proveedor de modelo desconocido: '{cfg.name}' "
            f"(esperado fake|anthropic|openai|deepseek|ollama|llama_cpp|gemini)"
        )
