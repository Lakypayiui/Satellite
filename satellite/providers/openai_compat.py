"""Proveedor OpenAI-compatible (OpenAI, DeepSeek, Ollama, llama.cpp, Gemini)."""

from __future__ import annotations

import json
from typing import Any

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse, Message, Role, Usage
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider
from satellite.providers.capabilities import (
    EFFORT_PARAM,
    OUTPUT_CONFIG_EFFORT_PARAM,
    model_capabilities,
    resolve_effort,
    resolve_think,
)
from satellite.providers.parsing import coerce_arguments

# Parámetros de esfuerzo que este adaptador genérico sabe enviar. Los controles propios de un
# proveedor (``thinking_level`` de Gemini nativo, ``thinking``/``budget_tokens`` de Anthropic)
# **no** se mandan desde aquí: en la forma OpenAI-compatible no son válidos (HTTP 400).
_OPENAI_SENDABLE_EFFORT: frozenset[str] = frozenset({EFFORT_PARAM, OUTPUT_CONFIG_EFFORT_PARAM})


def _set_effort(kwargs: dict, param: str, value: object) -> None:
    """Coloca el nivel en ``param``, admitiendo rutas anidadas (``output_config.effort``)."""
    if "." in param:
        head, _, tail = param.partition(".")
        section = kwargs.get(head)
        if not isinstance(section, dict):
            section = {}
            kwargs[head] = section
        section[tail] = value
    else:
        kwargs[param] = value


class OpenAICompatProvider(LLMProvider):
    """Traduce el formato interno al protocolo de chat completions OpenAI."""

    def __init__(
        self,
        config: "ProviderConfig",
        client: Any = None,
        timeout_s: int = 120,
        limits=None,
        strategy=None,
    ) -> None:
        self.config = config
        self.timeout_s = timeout_s
        self.limits = limits
        self.strategy = strategy
        if client is not None:
            self._client = client
        else:
            import openai

            api_key = config.api_key or "not-needed"
            self._client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=config.base_url if config.base_url else None,
                timeout=timeout_s,
            )

    @property
    def automatic_cache(self) -> bool:
        """OpenAI cachea el prefijo automáticamente (sin marcadores)."""
        return True

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Traduce la petición a formato OpenAI y normaliza la respuesta."""
        messages = [_to_openai_message(m) for m in request.messages]
        caps = model_capabilities(
            self.config.model, getattr(self.config, "model_overrides", None)
        )
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
        }
        # los modelos de razonamiento rechazan `temperature` no estándar
        if caps.accepts_temperature:
            kwargs["temperature"] = request.temperature
        # esfuerzo de razonamiento: sólo si el modelo lo admite, el nivel está entre los suyos y
        # el canal es uno que este adaptador sabe enviar. "auto" = no enviar nada; "off" = el
        # valor que apaga el razonamiento (p. ej. `none`). `resolve_effort` devuelve None cuando
        # no aplica, así que nunca se manda un valor inválido ni un parámetro ajeno.
        effort = resolve_effort(
            self.config.model,
            getattr(self.config, "model_overrides", None),
            getattr(self.config, "reasoning_effort", None) or "",
            thinking=getattr(self.config, "thinking", None),
        )
        effort_param = caps.effort_param or EFFORT_PARAM
        if effort and effort_param in _OPENAI_SENDABLE_EFFORT:
            _set_effort(kwargs, effort_param, effort)
        # Interruptor «Think» explícito (p. ej. DeepSeek ``thinking: {type: ...}``): sólo si el
        # modelo declara ese canal; si no, no se envía nada (nunca se finge).
        think = resolve_think(
            self.config.model,
            getattr(self.config, "model_overrides", None),
            getattr(self.config, "thinking", None),
        )
        if think:
            kwargs.update(think)
        if request.tools is not None:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        limit = request.max_tokens
        if limit is None:
            # Sin límite en la petición, se usa el del proveedor guardado (si lo hay).
            limit = getattr(self.config, "max_tokens", None)
        if limit is not None:
            # `max_tokens` no existe en las familias de razonamiento
            kwargs[caps.token_param] = limit
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — el provider envuelve cualquier error
            raise ProviderError(
                f"error del proveedor OpenAI-compat: {exc}"
            ) from exc
        return _from_openai_response(resp)


def _to_openai_message(m: Message) -> dict:
    """Convierte un Message interno a formato OpenAI."""
    if m.role is Role.SYSTEM:
        return {"role": "system", "content": m.content}
    if m.role is Role.TOOL:
        return {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
    if m.role is Role.ASSISTANT:
        msg: dict = {"role": "assistant", "content": m.content}
        if m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
        return msg
    return {"role": "user", "content": m.content}


def _from_openai_response(resp: Any) -> LLMResponse:
    """Convierte una respuesta del SDK OpenAI a LLMResponse."""
    choice = resp.choices[0]
    message = choice.message
    content = message.content
    tool_calls = None
    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls:
        tool_calls = tuple(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=coerce_arguments(tc.function.arguments),
            )
            for tc in raw_calls
        )
    usage = None
    raw_usage = getattr(resp, "usage", None)
    if raw_usage is not None:
        details = getattr(raw_usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details is not None else None
        usage = Usage(
            prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(raw_usage, "total_tokens", 0) or 0,
            cache_read_input_tokens=None if cached is None else int(cached),
        )
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        stop_reason=getattr(choice, "finish_reason", "") or "",
        usage=usage,
    )
