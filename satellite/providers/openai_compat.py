"""Proveedor OpenAI-compatible (OpenAI, DeepSeek, Ollama, llama.cpp, Gemini)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, AsyncIterator

from satellite.core.errors import ProviderError
from satellite.core.messages import (
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    StreamChunk,
    Usage,
)
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

if TYPE_CHECKING:
    from satellite.providers.factory import ProviderConfig

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
                # Los reintentos los hace ``RetryingProvider`` (con aviso a la UI).
                max_retries=0,
            )

    @property
    def automatic_cache(self) -> bool:
        """OpenAI cachea el prefijo automáticamente (sin marcadores)."""
        return True

    def _kwargs(self, request: LLMRequest) -> dict:
        """Parámetros de ``chat.completions.create`` para esta petición."""
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
            # El SDK OpenAI valida los argumentos nombrados de ``create`` y rechaza
            # parámetros específicos de proveedores compatibles, como el de DeepSeek
            # ``thinking``. ``extra_body`` los añade al JSON sin pasar por esa firma.
            kwargs.setdefault("extra_body", {}).update(think)
        # Only send `tools` when there is at least one: several compatible providers
        # reject an empty array ("tools must not be empty"), which looked like a model
        # failure during synthesis (no tools) and aborted the node.
        if request.tools:
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
        return kwargs

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Traduce la petición a formato OpenAI y normaliza la respuesta."""
        kwargs = self._kwargs(request)
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — el provider envuelve cualquier error
            raise ProviderError(
                f"error del proveedor OpenAI-compat: {exc}"
            ) from exc
        return _from_openai_response(resp)

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Streaming de chat completions: cede los deltas de texto y la respuesta final.

        Los servidores compatibles no siempre aceptan ``stream`` (o ``stream_options``).
        Si el stream falla **antes** de ceder nada, se repite la llamada sin streaming:
        el usuario pierde el progreso, no la respuesta.
        """
        kwargs = self._kwargs(request)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        acc = _StreamAccumulator()
        started = False
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for event in stream:
                text = acc.add(event)
                if text:
                    started = True
                    yield StreamChunk(text=text)
        except Exception as exc:  # noqa: BLE001 — el provider envuelve cualquier error
            if started:
                raise ProviderError(
                    f"error del proveedor OpenAI-compat durante el stream: {exc}"
                ) from exc
            if not _stream_unsupported(exc):
                # Un 429, un 5xx o un corte son transitorios: los reintenta (con espera
                # y ``Retry-After``) el envoltorio de reintentos, no una segunda petición
                # inmediata que duplica la latencia y se salta la espera.
                raise ProviderError(f"error del proveedor OpenAI-compat: {exc}") from exc
            response = await self.complete(request)
            if response.content:
                yield StreamChunk(text=response.content)
            yield StreamChunk(response=response)
            return
        yield StreamChunk(response=acc.response())


def _stream_unsupported(exc: BaseException) -> bool:
    """True si el servidor rechazó el streaming en sí (y vale repetir sin él).

    Un 4xx de petición mal formada que menciona ``stream``: p. ej. «Unsupported
    parameter: stream_options». Un 429, un 5xx, una clave inválida o un corte de red
    no lo son.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(status, int) or not 400 <= status < 500:
        return False
    if status in (401, 403, 408, 429):
        return False
    return "stream" in str(exc).lower()


class _StreamAccumulator:
    """Reconstruye una respuesta de chat completions a partir de sus chunks."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.calls: dict[int, dict] = {}
        self.finish_reason = ""
        self.usage = None

    def add(self, event: Any) -> str:
        raw_usage = getattr(event, "usage", None)
        if raw_usage is not None:
            self.usage = raw_usage
        choices = getattr(event, "choices", None) or []
        if not choices:
            return ""
        choice = choices[0]
        finish = getattr(choice, "finish_reason", None)
        if finish:
            self.finish_reason = str(finish)
        delta = getattr(choice, "delta", None)
        if delta is None:
            return ""
        for call in getattr(delta, "tool_calls", None) or []:
            index = int(getattr(call, "index", 0) or 0)
            slot = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if getattr(call, "id", None):
                slot["id"] = str(call.id)
            function = getattr(call, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    slot["name"] += str(function.name)
                if getattr(function, "arguments", None):
                    slot["arguments"] += str(function.arguments)
        text = getattr(delta, "content", None)
        if text:
            self.text.append(str(text))
            return str(text)
        return ""

    def response(self) -> LLMResponse:
        tool_calls = tuple(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=coerce_arguments(slot["arguments"]),
            )
            for index, slot in sorted(self.calls.items())
        )
        return LLMResponse(
            content="".join(self.text) or None,
            tool_calls=tool_calls or None,
            stop_reason=self.finish_reason,
            usage=_usage_from(self.usage),
        )


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
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        stop_reason=getattr(choice, "finish_reason", "") or "",
        usage=_usage_from(getattr(resp, "usage", None)),
    )


def _usage_from(raw_usage: Any) -> Usage | None:
    """Normaliza el ``usage`` del SDK OpenAI (``None`` si no vino)."""
    if raw_usage is None:
        return None
    details = getattr(raw_usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    return Usage(
        prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(raw_usage, "total_tokens", 0) or 0,
        cache_read_input_tokens=None if cached is None else int(cached),
    )
