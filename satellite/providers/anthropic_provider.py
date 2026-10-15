"""Proveedor Anthropic (Claude) con traducción al formato Messages API.

Incluye la estrategia de prompt caching: coloca `cache_control` en
(a) fin de tools+system, (b) fin del catálogo estable y (c) último bloque del
historial, respetando el máximo del proveedor y sin marcar prefijos por debajo
del mínimo del modelo.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse, Message, Role, Usage
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider
from satellite.providers.caching import (
    AnthropicCacheStrategy,
    CacheLimits,
    CachePlan,
    CacheStrategy,
    TtlLedger,
    limits_for,
)
from satellite.providers.parsing import coerce_arguments
from satellite.providers.prefix import estimate_prefix_tokens, prefix_hash, prefix_payload

# Anthropic **exige** `max_tokens` en toda petición: sin él la API responde 400.
# Es solo el último recurso; lo que configure el usuario manda.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    """Traduce el formato interno a la API de mensajes de Anthropic."""

    def __init__(
        self,
        config: "ProviderConfig",
        client: Any = None,
        timeout_s: int = 120,
        limits: CacheLimits | None = None,
        strategy: CacheStrategy | None = None,
    ) -> None:
        self.config = config
        self.timeout_s = timeout_s
        self.strategy = strategy or AnthropicCacheStrategy()
        self.limits = limits or limits_for("anthropic", getattr(config, "model", ""))
        self._ttl_ledger = TtlLedger()
        self.last_plan: CachePlan | None = None
        self.last_ttl_degraded = False
        if client is not None:
            self._client = client
        else:
            import anthropic

            self._client = anthropic.AsyncAnthropic(
                api_key=config.api_key or "not-needed",
                timeout=timeout_s,
            )

    @property
    def automatic_cache(self) -> bool:
        return False

    def cache_strategy(self) -> CacheStrategy:
        return self.strategy

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Traduce la petición a formato Anthropic y normaliza la respuesta."""
        system_texts = [m.content for m in request.messages if m.role is Role.SYSTEM]
        others = [m for m in request.messages if m.role is not Role.SYSTEM]
        stable = min(max(0, request.stable_user_count), len(others))
        catalog = others[stable - 1].content if stable > 0 else None

        plan = self._plan(request, system_texts, catalog, others, stable)
        self.last_plan = plan
        marker = self.strategy.marker(plan.ttl)

        messages = [_to_anthropic_message(m) for m in others]
        if plan.mark_catalog and stable > 0:
            messages[stable - 1] = _mark_last_block(messages[stable - 1], marker)
        if plan.mark_tail and messages:
            messages[-1] = _mark_last_block(messages[-1], marker)

        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if system_texts:
            kwargs["system"] = _system_blocks(
                system_texts, marker if plan.mark_system else None
            )
        if request.tools is not None:
            tools = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
            if plan.mark_system and tools and not system_texts:
                tools[-1] = {**tools[-1], "cache_control": marker}
            kwargs["tools"] = tools
        limit = request.max_tokens
        if limit is None:
            limit = getattr(self.config, "max_tokens", None)
        if limit is None:
            limit = DEFAULT_MAX_OUTPUT_TOKENS
        kwargs["max_tokens"] = limit
        try:
            resp = await self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"error del proveedor Anthropic: {exc}") from exc
        return _from_anthropic_response(resp)

    def _plan(
        self,
        request: LLMRequest,
        system_texts: list[str],
        catalog: str | None,
        others: list[Message],
        stable: int,
    ) -> CachePlan:
        """Calcula el plan de breakpoints y valida el TTL."""
        system_text = "\n".join(system_texts)
        payload = prefix_payload(request.tools, system_text, catalog)
        plan = self.strategy.plan(
            prefix_tokens=estimate_prefix_tokens(payload),
            has_system=bool(system_texts) or bool(request.tools),
            has_catalog=catalog is not None,
            has_tail=len(others) - stable > 1,
            limits=self.limits,
        )
        if plan.count == 0:
            return plan
        digest = prefix_hash(request.tools, system_text, catalog)
        ttl, degraded = self._ttl_ledger.effective(digest, plan.ttl)
        self.last_ttl_degraded = degraded
        return replace(plan, ttl=ttl) if degraded else plan


def _extract_system(messages: tuple[Message, ...]) -> str | None:
    """El primer mensaje system (compatibilidad)."""
    for message in messages:
        if message.role is Role.SYSTEM:
            return message.content
    return None


def _system_blocks(texts: list[str], marker: dict | None) -> list[dict]:
    """Bloques de system; el último lleva `cache_control` si toca marcarlo."""
    blocks = [{"type": "text", "text": text} for text in texts]
    if marker is not None and blocks:
        blocks[-1] = {**blocks[-1], "cache_control": marker}
    return blocks


def _mark_last_block(message: dict, marker: dict) -> dict:
    """Copia del mensaje con `cache_control` en su último bloque."""
    marked = dict(message)
    content = marked.get("content")
    if isinstance(content, str):
        blocks: list[dict] = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = [dict(block) for block in content]
    else:
        blocks = []
    if not blocks:
        blocks = [{"type": "text", "text": ""}]
    blocks[-1] = {**blocks[-1], "cache_control": marker}
    marked["content"] = blocks
    return marked


def _to_anthropic_message(m: Message) -> dict:
    """Convierte un Message interno a un mensaje Anthropic."""
    if m.role is Role.USER:
        return {"role": "user", "content": m.content}
    if m.role is Role.TOOL:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                }
            ],
        }
    if m.role is Role.ASSISTANT:
        content: list[dict] = []
        if m.content:
            content.append({"type": "text", "text": m.content})
        for tc in m.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                }
            )
        return {"role": "assistant", "content": content}
    # system ya se manejó aparte
    return {"role": "user", "content": m.content}


def _from_anthropic_response(resp: Any) -> LLMResponse:
    """Convierte una respuesta Anthropic a LLMResponse."""
    content_parts = []
    tool_calls = []
    for block in resp.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            content_parts.append(str(getattr(block, "text", "")))
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    arguments=coerce_arguments(getattr(block, "input", {})),
                )
            )
    usage = None
    raw_usage = getattr(resp, "usage", None)
    if raw_usage is not None:
        usage = _normalise_anthropic_usage(raw_usage)
    stop_reason = getattr(resp, "stop_reason", "") or ""
    if stop_reason == "tool_use":
        stop_reason = "tool_calls"
    elif stop_reason == "end_turn":
        stop_reason = "stop"
    return LLMResponse(
        content="".join(content_parts) if content_parts else None,
        tool_calls=tuple(tool_calls) if tool_calls else None,
        stop_reason=stop_reason,
        usage=usage,
    )


def _normalise_anthropic_usage(raw_usage: Any) -> Usage:
    """Normaliza el usage de Anthropic.

    Anthropic reporta `input_tokens` **sin** contar lo cacheado, así que el
    input total interno es ``input + cache_read + cache_creation``.
    """
    input_tokens = int(getattr(raw_usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(raw_usage, "output_tokens", 0) or 0)
    creation = getattr(raw_usage, "cache_creation_input_tokens", None)
    read = getattr(raw_usage, "cache_read_input_tokens", None)
    creation = None if creation is None else int(creation)
    read = None if read is None else int(read)
    total_input = input_tokens + (creation or 0) + (read or 0)
    return Usage(
        prompt_tokens=total_input,
        completion_tokens=output_tokens,
        total_tokens=total_input + output_tokens,
        cache_creation_input_tokens=creation,
        cache_read_input_tokens=read,
    )
