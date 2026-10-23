"""Proveedor Anthropic (Claude) con traducción al formato Messages API.

Incluye la estrategia de prompt caching: coloca `cache_control` en
(a) fin de tools+system, (b) fin del catálogo estable y (c) último bloque del
historial, respetando el máximo del proveedor y sin marcar prefijos por debajo
del mínimo del modelo.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
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
from satellite.providers.caching import (
    ANCHOR_OFFSET,
    MODE_AUTO,
    MODE_EXPLICIT,
    VALID_MODES,
    AnthropicCacheStrategy,
    CacheLimits,
    CachePlan,
    CacheStrategy,
    TtlLedger,
    limits_for,
)
from satellite.providers.capabilities import override_entry, resolve_effort, thinking_payload
from satellite.providers.parsing import coerce_arguments
from satellite.providers.prefix import estimate_prefix_tokens, prefix_hash, prefix_payload

if TYPE_CHECKING:
    from satellite.providers.factory import ProviderConfig

# Anthropic **exige** `max_tokens` en toda petición: sin él la API responde 400.
# Es solo el último recurso; lo que configure el usuario manda.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


def normalize_base_url(value: object) -> str:
    """URL base del endpoint, sin duplicar ``/v1`` (el SDK añade ``/v1/messages``).

    Misma regla que `config/models.py` aplica a ``/v1/models``.
    """
    url = str(value or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url


class AnthropicProvider(LLMProvider):
    """Traduce el formato interno a la API de mensajes de Anthropic."""

    def __init__(
        self,
        config: "ProviderConfig",
        client: Any = None,
        timeout_s: int = 120,
        limits: CacheLimits | None = None,
        strategy: CacheStrategy | None = None,
        cache_options: dict | None = None,
    ) -> None:
        self.config = config
        self.timeout_s = timeout_s
        self.strategy = strategy or AnthropicCacheStrategy()
        self.limits = limits or limits_for("anthropic", getattr(config, "model", ""))
        self._ttl_ledger = TtlLedger()
        self.last_plan: CachePlan | None = None
        self.last_ttl_degraded = False
        self.last_prefix_hash = ""
        # `llm.cache`: modo (`explicit`|`auto`), diagnóstico y pre-calentado.
        self.cache_options = dict(cache_options or {})
        # Id de la respuesta anterior por prefijo (para el diagnóstico de la API).
        self._response_ids: dict[str, str] = {}
        if client is not None:
            self._client = client
        else:
            import anthropic

            kwargs: dict = {
                "api_key": config.api_key or "not-needed",
                "timeout": timeout_s,
                # Los reintentos los hace ``RetryingProvider`` (con aviso a la UI).
                "max_retries": 0,
            }
            base_url = normalize_base_url(getattr(config, "base_url", None))
            if base_url:
                kwargs["base_url"] = base_url
            self._client = anthropic.AsyncAnthropic(**kwargs)

    @property
    def automatic_cache(self) -> bool:
        return False

    def cache_strategy(self) -> CacheStrategy:
        return self.strategy

    def _kwargs(self, request: LLMRequest) -> dict:
        """Parámetros de ``messages.create`` (y el plan de caché en ``last_plan``)."""
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
        if plan.mark_anchor and len(messages) > ANCHOR_OFFSET:
            # Ancla rodante: la API solo mira 20 bloques hacia atrás, así que en
            # conversaciones largas hace falta una marca intermedia.
            index = len(messages) - 1 - ANCHOR_OFFSET
            messages[index] = _mark_last_block(messages[index], marker)
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
        # Same as the OpenAI-compatible adapter: an empty `tools` array is invalid for
        # this API, so only send the field when there is something to send.
        if request.tools:
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
        if self.cache_mode() == MODE_AUTO:
            # Caching automático: un `cache_control` de nivel superior y la API
            # mueve el breakpoint sola (no se marcan bloques).
            kwargs["cache_control"] = marker

        # Thinking extendido: sólo si se pidió un nivel y el modelo lo declara (override en
        # `llm.models.<id>`); "auto" no envía nada, así el comportamiento por defecto no
        # cambia. Anthropic exige `max_tokens` > `budget_tokens` y no admite `temperature`
        # junto al thinking, así que la temperatura se omite cuando va activo.
        overrides = getattr(self.config, "model_overrides", None)
        level = resolve_effort(
            self.config.model, overrides, getattr(self.config, "reasoning_effort", None) or ""
        )
        if level is not None:
            thinking = thinking_payload(level, override_entry(self.config.model, overrides))
            if thinking is not None:
                budget = int(thinking.get("budget_tokens", 0) or 0)
                if budget and limit <= budget:
                    limit = budget + 1024
                    kwargs["max_tokens"] = limit
                kwargs["thinking"] = thinking
                kwargs.pop("temperature", None)
        return kwargs

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Traduce la petición a formato Anthropic y normaliza la respuesta."""
        kwargs = self._kwargs(request)
        create = self._diagnostics_create()
        sent = None
        if create is not None:
            previous = self._response_ids.get(self.last_prefix_hash, "")
            # Primera vez de este prefijo: `null` para optar por el diagnóstico.
            sent = {"previous_message_id": previous or None}
        try:
            if create is not None:
                resp = await create(**kwargs, diagnostics=sent)
            else:
                resp = await self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"error del proveedor Anthropic: {exc}") from exc
        if create is not None:
            response_id = str(getattr(resp, "id", "") or "")
            if response_id:
                self._response_ids[self.last_prefix_hash] = response_id
        return _from_anthropic_response(resp, diagnostics=sent)

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Streaming de la API de mensajes: deltas de texto y la respuesta final.

        Con el diagnóstico de caché activo (ruta beta, opt-in) se usa ``complete``: el
        diagnóstico viene en la respuesta entera, no en los eventos del stream.
        """
        if self._diagnostics_create() is not None:
            async for chunk in super().stream(request):
                yield chunk
            return
        kwargs = self._kwargs(request)
        acc = _AnthropicStreamAccumulator()
        try:
            events = await self._client.messages.create(**kwargs, stream=True)
            async for event in events:
                text = acc.add(event)
                if text:
                    yield StreamChunk(text=text)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"error del proveedor Anthropic: {exc}") from exc
        yield StreamChunk(response=_from_anthropic_response(acc.message()))


    # --- diagnóstico de caché (opt-in, best-effort) ---

    def cache_mode(self) -> str:
        """Modo de caché pedido (``explicit`` por defecto)."""
        mode = str(self.cache_options.get("mode", "") or "").lower()
        return mode if mode in VALID_MODES else MODE_EXPLICIT

    def _diagnostics_create(self):
        """``create`` de la ruta beta si el diagnóstico está activo; si no, ``None``.

        Es observacional: si el cliente no expone la ruta beta, simplemente no se
        pide el diagnóstico (nunca bloquea la llamada).
        """
        if not self.cache_options.get("diagnostics"):
            return None
        beta = getattr(self._client, "beta", None)
        create = getattr(getattr(beta, "messages", None), "create", None)
        return create if callable(create) else None

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
            blocks=len(request.messages),
            mode=self.cache_mode(),
        )
        digest = prefix_hash(request.tools, system_text, catalog)
        # El diagnóstico de la API se recuerda por prefijo, así que se calcula
        # siempre (aunque no se marque ningún breakpoint, p. ej. en modo auto).
        self.last_prefix_hash = digest
        if plan.count == 0:
            return plan
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


def _from_anthropic_response(resp: Any, diagnostics: dict | None = None) -> LLMResponse:
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
        response_id=str(getattr(resp, "id", "") or ""),
        cache_diagnostics=_diagnostics_payload(resp, diagnostics),
    )


def _diagnostics_payload(resp: Any, sent: dict | None) -> dict | None:
    """Normaliza el `diagnostics` de la respuesta (o ``None`` si no se pidió).

    Tres casos según la documentación: el campo **ausente** (no se pidió, primer
    turno, o comparó y no hay divergencia), ``{"cache_miss_reason": null}`` (la
    comparación sigue en curso) y un motivo con ``type``.
    """
    if sent is None:
        return None
    previous = sent.get("previous_message_id")
    raw = getattr(resp, "diagnostics", None)
    if raw is None:
        return {"previous_message_id": previous, "present": False, "cache_miss_reason": None}
    reason = getattr(raw, "cache_miss_reason", None)
    return {
        "previous_message_id": previous,
        "present": True,
        "cache_miss_reason": (
            None
            if reason is None
            else {
                "type": str(getattr(reason, "type", "") or ""),
                "cache_missed_input_tokens": getattr(reason, "cache_missed_input_tokens", None),
            }
        ),
    }


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


class _AnthropicStreamAccumulator:
    """Reconstruye un mensaje Anthropic a partir de los eventos del stream."""

    def __init__(self) -> None:
        self.id = ""
        self.blocks: dict[int, SimpleNamespace] = {}
        self.json_parts: dict[int, list[str]] = {}
        self.usage: dict = {}
        self.stop_reason = ""

    def add(self, event: Any) -> str:
        kind = getattr(event, "type", "")
        if kind == "message_start":
            message = getattr(event, "message", None)
            self.id = str(getattr(message, "id", "") or "")
            self._merge_usage(getattr(message, "usage", None))
        elif kind == "content_block_start":
            index = int(getattr(event, "index", 0) or 0)
            block = getattr(event, "content_block", None)
            block_type = getattr(block, "type", "")
            self.blocks[index] = SimpleNamespace(
                type=block_type,
                text=str(getattr(block, "text", "") or ""),
                id=str(getattr(block, "id", "") or ""),
                name=str(getattr(block, "name", "") or ""),
                input=getattr(block, "input", None) or {},
            )
        elif kind == "content_block_delta":
            index = int(getattr(event, "index", 0) or 0)
            delta = getattr(event, "delta", None)
            delta_type = getattr(delta, "type", "")
            block = self.blocks.setdefault(
                index, SimpleNamespace(type="text", text="", id="", name="", input={})
            )
            if delta_type == "text_delta":
                text = str(getattr(delta, "text", "") or "")
                block.text += text
                return text
            if delta_type == "input_json_delta":
                self.json_parts.setdefault(index, []).append(
                    str(getattr(delta, "partial_json", "") or "")
                )
        elif kind == "message_delta":
            delta = getattr(event, "delta", None)
            self.stop_reason = str(getattr(delta, "stop_reason", "") or self.stop_reason)
            self._merge_usage(getattr(event, "usage", None))
        return ""

    def _merge_usage(self, raw: Any) -> None:
        if raw is None:
            return
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            value = getattr(raw, key, None)
            if value is not None:
                self.usage[key] = value

    def message(self) -> SimpleNamespace:
        content = []
        for index in sorted(self.blocks):
            block = self.blocks[index]
            if index in self.json_parts:
                block.input = "".join(self.json_parts[index]) or "{}"
            content.append(block)
        return SimpleNamespace(
            id=self.id,
            content=content,
            usage=SimpleNamespace(**self.usage) if self.usage else None,
            stop_reason=self.stop_reason,
        )
