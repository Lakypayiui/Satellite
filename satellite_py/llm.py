"""LLM client factory for the Python Satellite runtime.

Resolves the LLM provider from a project ``.satellite/config/config.json``
(or environment variables as a fallback) and exposes a small ``complete()``
interface so callers do not depend on a specific SDK.

Supported providers (mirroring the C++ ``ProviderFactory``):

- ``local`` / ``llama.cpp`` style servers exposing an OpenAI-compatible
  ``/v1/chat/completions`` endpoint (default ``http://localhost:8080``).
- ``openai`` and ``openai-compatible`` (any ``base_url`` + ``api_key``).
- ``deepseek`` (an OpenAI-compatible API with its own default base URL).
- ``anthropic`` / ``claude`` (messages API).

Configuration keys (``.satellite/config/config.json``)::

    {
      "llm": {
        "provider": "local",            // local | openai | openai-compatible |
                                        // deepseek | anthropic | claude
        "model": "gpt-4o-mini",
        "base_url": "http://localhost:8080",   // for local/compatible
        "api_key": "",                  // optional (local), or literal key
        "api_key_env": "OPENAI_API_KEY" // env var holding the key
      },
      "local_llm": {
        "port": 8080,                   // used when provider == local
        "model": "gemma-2b-it",
        "api_key": ""
      }
    }

Environment fallbacks: ``SATELLITE_LLM_PROVIDER``, ``OPENAI_API_KEY`` /
``OPENAI_BASE_URL`` / ``OPENAI_MODEL``, ``ANTHROPIC_API_KEY`` /
``ANTHROPIC_MODEL``, ``DEEPSEEK_API_KEY``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

# API-compatible providers that use the OpenAI chat-completions wire format.
_OPENAI_WIRE = {"openai", "openai-compatible", "deepseek", "local", "llama.cpp"}


class LLMClient(Protocol):
    """Minimal chat completion interface used by Satellite Python."""

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        """Return the assistant text for one exchange."""
        ...


class OpenAICompatibleClient:
    """Chat-completions client for any OpenAI-compatible endpoint.

    Works for OpenAI, DeepSeek, llama.cpp / Ollama-style local servers and
    any other ``/v1/chat/completions`` implementation.
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
    ) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        content = response.choices[0].message.content
        return content or ""


class AnthropicClient:
    """Anthropic Messages API client."""

    def __init__(self, api_key: str = "", model: str = "claude-3-5-sonnet-latest") -> None:
        from anthropic import Anthropic

        self.model = model
        self._client = Anthropic(api_key=api_key or None)

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")


class LLMConfig:
    """Resolved LLM provider settings."""

    def __init__(self, provider: str, model: str, base_url: str = "", api_key: str = "") -> None:
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key

    def create_client(self) -> LLMClient:
        provider = self.provider
        if provider in {"anthropic", "claude"}:
            return AnthropicClient(api_key=self.api_key, model=self.model)
        if provider == "deepseek":
            base_url = self.base_url or "https://api.deepseek.com"
            if not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
            return OpenAICompatibleClient(base_url=base_url, api_key=self.api_key, model=self.model)
        # openai / openai-compatible / local: chat-completions OpenAI wire.
        if provider in {"local", "llama.cpp"}:
            base_url = self.base_url or "http://localhost:8080"
        else:
            base_url = self.base_url or "https://api.openai.com/v1"
        if not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        return OpenAICompatibleClient(base_url=base_url, api_key=self.api_key, model=self.model)


def load_llm_config(config_path: str | Path | None = None) -> LLMConfig:
    """Resolve provider settings from ``config.json`` (or environment).

    ``config_path`` defaults to ``<cwd>/.satellite/config/config.json`` when it
    exists; otherwise environment variables are used (the historical planner
    behaviour).
    """
    data: dict[str, Any] = {}
    if config_path is not None:
        path = Path(config_path)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
    else:
        default = Path.cwd() / ".satellite" / "config" / "config.json"
        if default.is_file():
            try:
                data = json.loads(default.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}

    llm = data.get("llm", {}) if isinstance(data.get("llm"), dict) else {}
    local_llm = data.get("local_llm", {}) if isinstance(data.get("local_llm"), dict) else {}
    use_local = bool(data.get("use_local_llm", False))

    provider = llm.get("provider") or os.getenv("SATELLITE_LLM_PROVIDER", "openai")
    if use_local:
        provider = "local"
    if provider in {"anthropic", "claude"}:
        model = llm.get("model") or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
        api_key = llm.get("api_key") or _key_from_env(llm.get("api_key_env")) or os.getenv("ANTHROPIC_API_KEY", "")
        return LLMConfig(provider="anthropic", model=str(model), api_key=api_key)

    # OpenAI-wire providers (openai, openai-compatible, deepseek, local).
    if provider in {"local", "llama.cpp"} or use_local:
        port = local_llm.get("port", 8080)
        model = (
            local_llm.get("model")
            or llm.get("model")
            or os.getenv("OPENAI_MODEL", "gemma-2b-it")
        )
        base_url = llm.get("base_url") or f"http://localhost:{port}"
        api_key = llm.get("api_key") or local_llm.get("api_key") or ""
        return LLMConfig(provider="local", model=str(model), base_url=str(base_url), api_key=str(api_key))

    if provider == "deepseek":
        model = llm.get("model") or os.getenv("OPENAI_MODEL", "deepseek-chat")
        api_key = llm.get("api_key") or _key_from_env(llm.get("api_key_env")) or os.getenv("DEEPSEEK_API_KEY", "")
        base_url = llm.get("base_url") or "https://api.deepseek.com"
        return LLMConfig(provider="deepseek", model=str(model), base_url=str(base_url), api_key=api_key)

    model = llm.get("model") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    api_key = llm.get("api_key") or _key_from_env(llm.get("api_key_env")) or os.getenv("OPENAI_API_KEY", "")
    base_url = llm.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if provider in {"openai-compatible", "openai"}:
        return LLMConfig(provider=provider, model=str(model), base_url=str(base_url), api_key=api_key)
    # Cualquier otro provider se trata como compatible con OpenAI.
    return LLMConfig(provider="openai-compatible", model=str(model), base_url=str(base_url), api_key=api_key)


def _key_from_env(env_name: Any) -> str:
    if isinstance(env_name, str) and env_name:
        return os.getenv(env_name, "")
    return ""
