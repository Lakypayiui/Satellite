"""Factory de proveedores LLM."""

from __future__ import annotations

from dataclasses import dataclass, field

from satellite.core.errors import ProviderError
from satellite.providers.anthropic_provider import AnthropicProvider
from satellite.providers.chatgpt_plan import ChatGPTPlanProvider
from satellite.providers.claude_code_plan import ClaudeCodePlanProvider
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
    # Nivel de esfuerzo de razonamiento pedido (``None``/``auto`` = no enviar nada).
    # Fuera de ``eq``/``hash``: es plumbing, no identidad.
    reasoning_effort: str | None = field(default=None, compare=False)
    # Interruptor «Think» explícito (``None`` = no tocar; ``True``/``False`` = encender/apagar
    # por el canal que declare el modelo). Fuera de ``eq``/``hash``: es plumbing.
    thinking: bool | None = field(default=None, compare=False)
    # ``llm.cache``: modo (explicit|auto), diagnóstico y pre-calentado.
    cache_options: dict = field(default_factory=dict, compare=False)
    # Authentication route for account-backed providers (never contains credentials).
    auth_type: str = field(default="", compare=False)


class ProviderFactory:
    """Crea el LLMProvider adecuado según el nombre de la config."""

    @staticmethod
    def create(cfg: ProviderConfig) -> LLMProvider:
        """Crea el provider correspondiente; ProviderError si es desconocido."""
        name = (cfg.name or "").lower()
        if name == "fake":
            return FakeLLM()
        if getattr(cfg, "auth_type", "") == "chatgpt_account":
            return ChatGPTPlanProvider(cfg)
        if getattr(cfg, "auth_type", "") == "claude_code_account":
            return ClaudeCodePlanProvider(cfg)
        if name == "anthropic":
            return AnthropicProvider(cfg, cache_options=getattr(cfg, "cache_options", None))
        if name in _OPENAI_COMPAT_NAMES or cfg.base_url:
            return OpenAICompatProvider(cfg)
        raise ProviderError(
            f"proveedor de modelo desconocido: '{cfg.name}' "
            f"(esperado fake|anthropic|openai|deepseek|ollama|llama_cpp|gemini)"
        )
