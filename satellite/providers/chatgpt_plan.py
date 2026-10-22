"""OpenAI Responses adapter using the user's explicitly authorized ChatGPT plan."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

import httpx

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse, Role, Usage
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider
from satellite.providers.parsing import coerce_arguments


class ChatGPTPlanProvider(LLMProvider):
    """Uses Sign in with ChatGPT tokens via the desktop's private local broker."""

    def __init__(self, config: "ProviderConfig", client: Any = None, timeout_s: int = 180) -> None:
        self.config = config
        self.timeout_s = timeout_s
        self._client = client

    @property
    def automatic_cache(self) -> bool:
        return True

    async def complete(self, request: LLMRequest) -> LLMResponse:
        broker = os.getenv("SATELLITE_ACCOUNT_BROKER_URL", "").rstrip("/")
        broker_secret = os.getenv("SATELLITE_ACCOUNT_BROKER_SECRET", "")
        if not broker or not broker_secret:
            raise ProviderError("Conecta tu cuenta de ChatGPT desde Ajustes de Satellite.")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                token_reply = await client.get(
                    f"{broker}/chatgpt/token",
                    headers={"Authorization": f"Bearer {broker_secret}"},
                )
                token_reply.raise_for_status()
                access_token = str(token_reply.json().get("access_token") or "")
                if not access_token:
                    raise ProviderError("La sesión de ChatGPT no devolvió un token de acceso.")
                payload = self._payload(request)
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", errors="replace")
                        raise ProviderError(self._error_message(response.status_code, body))
                    completed: dict | None = None
                    failure = ""
                    streamed_text = ""
                    streamed_deltas: list[str] = []
                    streamed_calls: list[ToolCall] = []
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        kind = str(event.get("type") or "")
                        if kind == "response.completed":
                            item = event.get("response")
                            completed = item if isinstance(item, dict) else None
                        elif kind in {"response.failed", "error"}:
                            item = event.get("response") if isinstance(event.get("response"), dict) else event
                            error = item.get("error") if isinstance(item, dict) else None
                            failure = str((error or {}).get("message") or (error or {}).get("code") or "falló la respuesta")
                        elif kind == "response.incomplete":
                            incomplete = event.get("response") if isinstance(event.get("response"), dict) else {}
                            details = incomplete.get("incomplete_details") or {}
                            failure = f"respuesta incompleta ({details.get('reason') or 'sin motivo'})"
                        elif kind == "response.output_text.delta":
                            streamed_deltas.append(str(event.get("delta") or ""))
                        elif kind == "response.output_text.done":
                            streamed_text = str(event.get("text") or streamed_text)
                        elif kind == "response.refusal.done":
                            streamed_text = str(event.get("refusal") or streamed_text)
                        elif kind in {
                            "response.output_item.done",
                            "response.function_call_arguments.done",
                        }:
                            # This account endpoint sends the real content in the
                            # streamed events and an empty `output` array in
                            # ``response.completed``. Collect it here instead.
                            if kind == "response.output_item.done":
                                item = event.get("item")
                            else:
                                # The arguments event has the call fields at the top
                                # level (not nested under an `item`).
                                item = {
                                    "type": "function_call",
                                    "call_id": event.get("call_id"),
                                    "item_id": event.get("item_id"),
                                    "name": event.get("name"),
                                    "arguments": event.get("arguments", {}),
                                }
                            if isinstance(item, dict):
                                text = self._text_from_item(item)
                                if text:
                                    streamed_text = text
                                call = self._call_from_item(item)
                                if call is not None and all(c.id != call.id for c in streamed_calls):
                                    streamed_calls.append(call)
                    if failure:
                        raise ProviderError(f"ChatGPT: {failure}")
                    if completed is None:
                        raise ProviderError("ChatGPT cerró el flujo sin completar la respuesta.")
                    result = self._response(completed)
                    fallback_text = streamed_text or "".join(streamed_deltas)
                    if not (result.content or "").strip() and fallback_text:
                        result = replace(result, content=fallback_text)
                    if not result.tool_calls and streamed_calls:
                        result = replace(
                            result,
                            tool_calls=tuple(streamed_calls),
                            stop_reason="tool_calls",
                        )
                    return result
        except ProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"No se pudo obtener la sesión de ChatGPT: HTTP {exc.response.status_code}.") from exc
        except Exception as exc:  # noqa: BLE001 — normalize network and parser errors
            raise ProviderError(f"Error al usar el plan de ChatGPT: {exc}") from exc

    def _payload(self, request: LLMRequest) -> dict:
        inputs: list[dict] = []
        for message in request.messages:
            if message.role is Role.TOOL:
                inputs.append({
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                })
                continue
            role = "developer" if message.role is Role.SYSTEM else message.role.value
            if message.content or not message.tool_calls:
                inputs.append({"role": role, "content": message.content})
            if message.role is Role.ASSISTANT:
                for call in message.tool_calls:
                    inputs.append({
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    })
        payload: dict = {
            "model": self.config.model,
            "input": inputs,
            "store": False,
            "stream": True,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                }
                for tool in request.tools
            ]
        # SIWC ChatGPT plan tokens currently reject max_output_tokens; let the
        # account endpoint apply its own output limit.
        effort = str(getattr(self.config, "reasoning_effort", "") or "").strip().lower()
        if effort and effort != "auto":
            payload["reasoning"] = {"effort": effort}
        return payload

    @staticmethod
    def _text_from_item(item: object) -> str:
        """Visible text carried by one Responses output item (message/text/refusal)."""
        if not isinstance(item, dict):
            return ""
        kind = str(item.get("type") or "")
        if kind == "output_text":
            return str(item.get("text") or "")
        if kind != "message":
            return ""
        parts: list[str] = []
        for block in item.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("output_text", "text"):
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "refusal":
                parts.append(str(block.get("refusal") or ""))
        return "".join(parts)

    @staticmethod
    def _call_from_item(item: object) -> ToolCall | None:
        """Function call carried by an output item / arguments event (or ``None``)."""
        if not isinstance(item, dict):
            return None
        if item.get("type") not in (None, "function_call"):
            return None
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        return ToolCall(
            id=str(item.get("call_id") or item.get("id") or item.get("item_id") or ""),
            name=name,
            arguments=coerce_arguments(item.get("arguments", {})),
        )

    @classmethod
    def _response(cls, data: dict) -> LLMResponse:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for item in data.get("output", []) or []:
            text = cls._text_from_item(item)
            if text:
                text_parts.append(text)
            call = cls._call_from_item(item)
            if call is not None:
                calls.append(call)
        # Some responses expose the convenience field even when `output` is empty.
        if not text_parts and isinstance(data.get("output_text"), str):
            text_parts.append(data["output_text"])
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        input_tokens = int(raw_usage.get("input_tokens") or 0)
        output_tokens = int(raw_usage.get("output_tokens") or 0)
        usage = Usage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=int(raw_usage.get("total_tokens") or input_tokens + output_tokens),
        )
        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tuple(calls) or None,
            stop_reason="tool_calls" if calls else "stop",
            usage=usage,
        )

    @staticmethod
    def _error_message(status: int, body: str) -> str:
        try:
            data = json.loads(body)
            error = data.get("error") if isinstance(data, dict) else None
            code = str((error or {}).get("code") or "")
            message = str((error or {}).get("message") or "")
            if code.startswith("subscription_sharing_usage_"):
                return "El uso disponible del plan de ChatGPT para Satellite se agotó o no está disponible; revísalo en Ajustes de ChatGPT."
            if message:
                return f"ChatGPT respondió HTTP {status}: {message}"
        except (json.JSONDecodeError, AttributeError):
            pass
        return f"ChatGPT respondió HTTP {status}."


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from satellite.providers.factory import ProviderConfig
