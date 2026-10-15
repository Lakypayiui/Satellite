"""Mecanismo único de parseo de tool calls y validación de argumentos."""

from __future__ import annotations

import json
import re
from typing import Any

from satellite.core.errors import ToolError
from satellite.core.tool_models import ToolCall, ToolSchema


def extract_tool_calls(raw: str | dict | list) -> list[ToolCall]:
    """Extrae ToolCalls de una respuesta del LLM.

    Acepta:
    - un dict ya parseado (formato del SDK: {"id", "name"/"function", "arguments"...});
    - un texto crudo con un bloque JSON embebido (delimitado por ```json o balanceado).
    Tolerante a marcado extra. Si no hay nada parseable → [].
    """
    if isinstance(raw, dict):
        return _from_dict(raw)
    if isinstance(raw, list):
        calls: list[ToolCall] = []
        for item in raw:
            calls.extend(_from_dict(item))
        return calls
    if isinstance(raw, str):
        parsed = _extract_json_from_text(raw)
        if parsed is None:
            return []
        if isinstance(parsed, dict):
            return _from_dict(parsed)
        if isinstance(parsed, list):
            calls = []
            for item in parsed:
                calls.extend(_from_dict(item))
            return calls
    return []


def _from_dict(item: dict) -> list[ToolCall]:
    """Normaliza un dict de tool call del SDK a ToolCall."""
    if "id" not in item:
        return []
    call_id = str(item["id"])
    name: Any = item.get("name") or item.get("tool_name")
    if name is None and isinstance(item.get("function"), dict):
        name = item["function"].get("name")
    if name is None:
        return []
    args_raw = item.get("arguments", {})
    if "function" in item and isinstance(item["function"], dict):
        args_raw = item["function"].get("arguments", args_raw)
    arguments = coerce_arguments(args_raw)
    if not isinstance(arguments, dict):
        return []
    return [ToolCall(id=call_id, name=str(name), arguments=arguments)]


def extract_json_object(text: str) -> dict | None:
    """Extrae el primer objeto JSON balanceado de un texto.

    Soporta bloques ```json ... ``` y cualquier texto con { ... } balanceado.
    """
    if not isinstance(text, str):
        return None
    # 1) bloque ```json
    match = re.search(r"```json\s*([\s\S]*?)```", text)
    candidates: list[str] = []
    if match:
        candidates.append(match.group(1).strip())
    # 2) primer { al último } balanceado
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_json_from_text(text: str) -> Any:
    """Extrae un objeto/lista JSON parseable de un texto (dict/list)."""
    stripped = text.strip()
    # bloques con marcado
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if match:
        candidate = match.group(1).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            pass
    # primer { o [ balanceado
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(stripped)):
            ch = stripped[i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, (dict, list)):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    break
    return None


def coerce_arguments(raw: Any) -> dict:
    """Convierte argumentos a dict; si llegan como string JSON, los parsea."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def validate_arguments(
    name: str, arguments: dict, schemas: list[ToolSchema]
) -> None:
    """Valida arguments contra el schema de la tool llamada.

    Si no hay schema para el nombre, no valida (el registry lo rechazará
    después). Si hay schema y falla → ToolError con detalle.
    """
    schema = next((s for s in schemas if s.name == name), None)
    if schema is None or not schema.parameters:
        return
    import jsonschema

    try:
        jsonschema.validate(arguments, schema.parameters)
    except jsonschema.ValidationError as exc:
        raise ToolError(
            f"argumentos inválidos para tool '{name}': {exc.message}"
        ) from exc
