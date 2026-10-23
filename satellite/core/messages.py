"""Formato interno normalizado de mensajes LLM (independiente del proveedor)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from satellite.core.errors import ContractError
from satellite.core.tool_models import ToolCall, ToolSchema


class Role(Enum):
    """Roles del protocolo de mensajes normalizado."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class Message:
    """Un mensaje normalizado. Determinadas reglas según rol."""

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            raise ContractError(
                f"Message: role debe ser Role, recibido {self.role!r}"
            )
        if self.role is Role.TOOL:
            if not self.tool_call_id:
                raise ContractError(
                    "Message: role TOOL requiere tool_call_id"
                )
        if self.role is Role.ASSISTANT and self.tool_calls:
            if self.content:
                # Permitido por contrato; no se prohíbe contenido junto a tool_calls.
                pass

    def to_dict(self) -> dict:
        return {
            "role": self.role.value,
            "content": self.content,
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "tool_call_id": self.tool_call_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        raw_role = d["role"]
        role = raw_role if isinstance(raw_role, Role) else Role(raw_role)
        return cls(
            role=role,
            content=d.get("content", ""),
            tool_calls=tuple(ToolCall.from_dict(t) for t in d.get("tool_calls", [])),
            tool_call_id=d.get("tool_call_id"),
        )


@dataclass(frozen=True)
class Usage:
    """Conteo de tokens de una llamada LLM.

    ``prompt_tokens`` es el **input total** (incluye lo servido desde cache).
    Los campos de cache son ``None`` cuando el proveedor **no los reporta**;
    ``0`` significa "reportado y sin cache".
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    @property
    def effective_input_tokens(self) -> int:
        """Input facturado a precio pleno (total menos lo leído de cache)."""
        return max(0, self.prompt_tokens - (self.cache_read_input_tokens or 0))

    @property
    def has_cache_data(self) -> bool:
        """True si el proveedor reportó información de cache."""
        return (
            self.cache_creation_input_tokens is not None
            or self.cache_read_input_tokens is not None
        )

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Usage":
        def _optional(key: str) -> int | None:
            value = d.get(key)
            return None if value is None else int(value)

        return cls(
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            cache_creation_input_tokens=_optional("cache_creation_input_tokens"),
            cache_read_input_tokens=_optional("cache_read_input_tokens"),
        )


@dataclass(frozen=True)
class CallContext:
    """Identidad de una llamada para métricas (ids y huellas; nunca contenido).

    ``spec_fp``/``catalog_fp`` permiten distinguir un cambio **legítimo** de
    catálogo/especificación de una **deriva** real del prefijo.
    """

    task_id: str = ""
    node_id: str = ""
    agent_id: str = ""
    role: str = ""
    spec_fp: str = ""
    catalog_fp: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "spec_fp": self.spec_fp,
            "catalog_fp": self.catalog_fp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CallContext":
        return cls(
            task_id=str(d.get("task_id", "")),
            node_id=str(d.get("node_id", "")),
            agent_id=str(d.get("agent_id", "")),
            role=str(d.get("role", "")),
            spec_fp=str(d.get("spec_fp", "")),
            catalog_fp=str(d.get("catalog_fp", "")),
        )


@dataclass(frozen=True)
class LLMRequest:
    """Petición a un proveedor LLM.

    ``stable_user_count`` declara cuántos mensajes USER iniciales forman el
    prefijo **estable** (p. ej. el catálogo del orquestador). Es información
    neutral sobre estabilidad, no sobre el proveedor.

    ``call`` es la identidad de la llamada (para métricas); puede ser ``None``
    cuando el llamante no la necesita (preprocesado, detección de chat).
    """

    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    stable_user_count: int = 0
    call: CallContext | None = None

    def __post_init__(self) -> None:
        if not self.messages:
            raise ContractError("LLMRequest: messages no puede estar vacío")
        if not isinstance(self.stable_user_count, int) or self.stable_user_count < 0:
            raise ContractError(
                f"LLMRequest: stable_user_count debe ser int >= 0, "
                f"recibido {self.stable_user_count!r}"
            )

    def to_dict(self) -> dict:
        return {
            "messages": [m.to_dict() for m in self.messages],
            "tools": [t.to_dict() for t in self.tools] if self.tools is not None else None,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stable_user_count": self.stable_user_count,
            "call": self.call.to_dict() if self.call else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LLMRequest":
        call = d.get("call")
        return cls(
            messages=tuple(Message.from_dict(m) for m in d["messages"]),
            tools=tuple(ToolSchema.from_dict(t) for t in d["tools"]) if d.get("tools") else None,
            temperature=float(d.get("temperature", 0.0)),
            max_tokens=d.get("max_tokens"),
            stable_user_count=int(d.get("stable_user_count", 0)),
            call=CallContext.from_dict(call) if isinstance(call, dict) else None,
        )


@dataclass(frozen=True)
class LLMResponse:
    """Respuesta de un proveedor LLM.

    ``response_id`` es el id opaco del proveedor (para diagnósticos de caché) y
    ``cache_diagnostics`` el informe opcional que compara con la petición anterior.
    """

    content: str | None = None
    tool_calls: tuple[ToolCall, ...] | None = None
    stop_reason: str = ""
    usage: Usage | None = None
    response_id: str = ""
    cache_diagnostics: dict | None = None

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "tool_calls": [t.to_dict() for t in self.tool_calls] if self.tool_calls else None,
            "stop_reason": self.stop_reason,
            "usage": self.usage.to_dict() if self.usage else None,
            "response_id": self.response_id,
            "cache_diagnostics": dict(self.cache_diagnostics or {}) or None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LLMResponse":
        calls = d.get("tool_calls")
        usage = d.get("usage")
        diagnostics = d.get("cache_diagnostics")
        return cls(
            content=d.get("content"),
            tool_calls=tuple(ToolCall.from_dict(t) for t in calls) if calls else None,
            stop_reason=d.get("stop_reason", ""),
            usage=Usage.from_dict(usage) if usage else None,
            response_id=str(d.get("response_id", "")),
            cache_diagnostics=dict(diagnostics) if isinstance(diagnostics, dict) else None,
        )


@dataclass(frozen=True)
class StreamChunk:
    """Un trozo de una respuesta en streaming.

    Los chunks intermedios llevan ``text`` (un delta de texto visible). El último lleva
    ``response``: la respuesta completa y normalizada, con el texto entero, las
    tool_calls, el uso y el ``stop_reason``. Quien consume el stream se queda con esa
    respuesta final; los deltas sólo sirven para mostrar progreso.
    """

    text: str = ""
    response: LLMResponse | None = None

    @property
    def final(self) -> bool:
        return self.response is not None
