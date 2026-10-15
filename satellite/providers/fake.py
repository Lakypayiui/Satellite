"""FakeLLM: proveedor determinista para tests."""

from __future__ import annotations

import json
from typing import Any

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse, Usage
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider


class FakeLLM(LLMProvider):
    """Proveedor determinista que sigue un script de respuestas.

    Cada paso del script admite ``content``, ``tool_calls``, ``error`` y
    ``usage`` (para poder simular aciertos de cache en los tests).
    """

    def __init__(
        self,
        script: list[dict] | None = None,
        default_content: str = "ok",
    ) -> None:
        self._script = list(script or [])
        self._default_content = default_content
        self.requests: list[LLMRequest] = []
        self._index = 0

    @property
    def reports_usage(self) -> bool:
        """True si algún paso del script reporta usage."""
        return any("usage" in step for step in self._script)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Devuelve la respuesta del script (consecutiva) o el default."""
        self.requests.append(request)
        if self._index < len(self._script):
            step = self._script[self._index]
            self._index += 1
        else:
            step = {"content": self._default_content}
        if "error" in step:
            raise ProviderError(str(step["error"]))
        content = step.get("content")
        tool_calls = step.get("tool_calls")
        calls = None
        if tool_calls is not None:
            calls = tuple(
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=_ensure_dict(tc.get("arguments", {})),
                )
                for tc in tool_calls
            )
        usage = None
        if isinstance(step.get("usage"), dict):
            usage = Usage.from_dict(step["usage"])
        return LLMResponse(
            content=content,
            tool_calls=calls,
            stop_reason="tool_calls" if calls else "end_turn",
            usage=usage,
        )


def _ensure_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}
