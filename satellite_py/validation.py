"""JSON Schema validation for Satellite agent payloads."""

from collections.abc import Mapping
import re
from typing import Any

from jsonschema import ValidationError, validate


def _validate(value: Any, schema: Mapping[str, Any], label: str) -> str | None:
    """Return a validation error message, or None when valid."""
    try:
        validate(instance=value, schema=dict(schema))
    except ValidationError as error:
        parts = list(error.absolute_path)
        # En errores de "required" la propiedad falta en la instancia,
        # así que se deriva del mensaje para apuntar a la ruta completa.
        if error.validator == "required":
            match = re.match(r"'([^']+)' is a required property", error.message)
            if match:
                parts.append(match.group(1))
        path = "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in parts
        ).lstrip(".")
        location = f" at {path}" if path else ""
        return f"{label} validation failed{location}: {error.message}"
    return None


def validate_input(value: Any, schema: Mapping[str, Any]) -> str | None:
    """Validate an agent input against its JSON Schema."""
    return _validate(value, schema, "input")


def validate_output(value: Any, schema: Mapping[str, Any]) -> str | None:
    """Validate an agent output against its JSON Schema."""
    return _validate(value, schema, "output")
