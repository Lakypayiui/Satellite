"""JSON Schema validation for Satellite agent payloads."""

from collections.abc import Mapping
from typing import Any

from jsonschema import ValidationError, validate


def _validate(value: Any, schema: Mapping[str, Any], label: str) -> str | None:
    """Return a validation error message, or None when valid."""
    try:
        validate(instance=value, schema=dict(schema))
    except ValidationError as error:
        path = "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
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
