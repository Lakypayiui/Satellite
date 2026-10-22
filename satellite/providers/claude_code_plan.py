"""Claude account adapter through Anthropic's official Claude Code SDK CLI."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse, Role, Usage
from satellite.core.tool_models import ToolCall
from satellite.providers.base import LLMProvider
from satellite.providers.parsing import coerce_arguments

_MODELS = {"sonnet", "opus", "haiku"}


async def _reap_process(process: asyncio.subprocess.Process) -> None:
    """Mata y espera al subproceso si sigue vivo, sin enmascarar el error original."""
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


class ClaudeCodePlanProvider(LLMProvider):
    """Uses the signed-in Claude Code account and keeps Satellite in charge of tools."""

    def __init__(self, config: "ProviderConfig", timeout_s: int = 240) -> None:
        self.config = config
        self.timeout_s = timeout_s

    async def complete(self, request: LLMRequest) -> LLMResponse:
        executable = shutil.which("claude")
        if not executable:
            raise ProviderError(
                "No se encontró Claude Code. Instálalo e inicia sesión con tu cuenta Claude; "
                "después reinicia Satellite."
            )
        model = str(self.config.model or "sonnet").strip()
        if model not in _MODELS:
            raise ProviderError("El modelo de Claude Code no es válido.")
        schema = self._output_schema(request)
        prompt = self._prompt(request)
        argv = [
            executable,
            "-p",
            "--output-format", "json",
            "--input-format", "stream-json",
            "--model", model,
            "--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            "--tools", "",
            "--disallowedTools", "mcp__*",
            "--no-session-persistence",
            "--max-turns", "1",
            "--bare",
        ]
        if sys.platform == "win32" and executable.lower().endswith((".cmd", ".bat")):
            argv = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)]
        env = dict(os.environ)
        # Prevent a machine-wide Console key from silently billing this account-backed adapter.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env.pop("ANTHROPIC_BASE_URL", None)
        payload = json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                },
            },
            ensure_ascii=False,
        ) + "\n"
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001 — normalize process startup errors
            raise ProviderError(f"No se pudo iniciar Claude Code: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload.encode("utf-8")), timeout=self.timeout_s
            )
        except asyncio.TimeoutError as exc:
            raise ProviderError("Claude Code tardó demasiado en responder.") from exc
        except Exception as exc:  # noqa: BLE001 — normalize process errors
            raise ProviderError(f"No se pudo comunicar con Claude Code: {exc}") from exc
        finally:
            # También cubre cancelación: wait_for propaga CancelledError y aquí se
            # impide que el proceso quede huérfano.
            await _reap_process(process)
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            lower = detail.lower()
            if "login" in lower or "authenticate" in lower or "not logged" in lower:
                detail = "Inicia sesión con tu cuenta Claude desde el botón de Ajustes."
            elif not detail:
                detail = f"Claude Code terminó con código {process.returncode}."
            raise ProviderError(f"Claude Code: {detail[:800]}")
        try:
            envelope = json.loads(stdout.decode("utf-8", errors="replace"))
            result = envelope.get("structured_output")
            if not isinstance(result, dict):
                raw_result = envelope.get("result")
                result = json.loads(raw_result) if isinstance(raw_result, str) else None
            if not isinstance(result, dict):
                raise ValueError("no se encontró structured_output en la respuesta")
            calls = []
            for item in result.get("tool_calls", []) or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                args = coerce_arguments(item.get("arguments", {}))
                calls.append(ToolCall(
                    id=str(item.get("id") or uuid.uuid4().hex),
                    name=name,
                    arguments=args if isinstance(args, dict) else {},
                ))
            usage_data = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
            input_tokens = int(usage_data.get("input_tokens") or 0)
            output_tokens = int(usage_data.get("output_tokens") or 0)
            usage = Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ) if input_tokens or output_tokens else None
            return LLMResponse(
                content=str(result.get("content") or ""),
                tool_calls=tuple(calls) or None,
                stop_reason="tool_calls" if calls else "stop",
                usage=usage,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("Claude Code devolvió una respuesta que Satellite no pudo interpretar.") from exc

    @staticmethod
    def _output_schema(request: LLMRequest) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tool_calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "arguments": {"type": "object"},
                        },
                        "required": ["name", "arguments"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["content", "tool_calls"],
            "additionalProperties": False,
        }

    @staticmethod
    def _prompt(request: LLMRequest) -> str:
        system = "\n\n".join(message.content for message in request.messages if message.role is Role.SYSTEM)
        transcript = []
        for message in request.messages:
            if message.role is Role.SYSTEM:
                continue
            transcript.append({
                "role": message.role.value,
                "content": message.content,
                "tool_calls": [call.to_dict() for call in message.tool_calls],
                "tool_call_id": message.tool_call_id,
            })
        tools = [tool.to_dict() for tool in (request.tools or ())]
        instruction = (
            "Return one JSON object that matches the required output schema. "
            "Use content for a user-facing response. Use tool_calls only when a listed Satellite "
            "tool should be called; provide its exact name and arguments matching its schema. "
            "Do not execute tools yourself. Keep content empty when requesting a tool."
        )
        parts = [instruction]
        if system:
            parts.append("System instructions:\n" + system)
        parts.append("Available Satellite tools (schemas):\n" + json.dumps(tools, ensure_ascii=False))
        parts.append("Conversation transcript:\n" + json.dumps(transcript, ensure_ascii=False))
        return "\n\n".join(parts)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from satellite.providers.factory import ProviderConfig
