"""Tests del parser único de tool calls."""

import pytest

from satellite.core.errors import ToolError
from satellite.core.tool_models import ToolSchema
from satellite.providers.parsing import (
    coerce_arguments,
    extract_json_object,
    extract_tool_calls,
    validate_arguments,
)


def test_extract_from_text_with_json_block() -> None:
    text = 'Antes\n```json\n{"id":"c1","name":"read_file","arguments":{"path":"a.txt"}}\n```\nDespués'
    calls = extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].id == "c1"
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "a.txt"}


def test_extract_from_dict_direct() -> None:
    d = {
        "id": "call_1",
        "name": "write_file",
        "arguments": {"path": "x", "content": "y"},
    }
    calls = extract_tool_calls(d)
    assert len(calls) == 1
    assert calls[0].name == "write_file"


def test_extract_from_openai_function_shape() -> None:
    d = {
        "id": "call_2",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"a"}'},
    }
    calls = extract_tool_calls(d)
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "a"}


def test_extract_with_noise_returns_empty() -> None:
    assert extract_tool_calls("sin nada aquí") == []
    assert extract_tool_calls("") == []


def test_extract_array_of_calls() -> None:
    raw = [
        {"id": "c1", "name": "read_file", "arguments": {"path": "a"}},
        {"id": "c2", "name": "write_file", "arguments": {"path": "b"}},
    ]
    calls = extract_tool_calls(raw)
    assert [c.id for c in calls] == ["c1", "c2"]


def test_coerce_arguments_from_string() -> None:
    assert coerce_arguments('{"a": 1}') == {"a": 1}
    assert coerce_arguments({"a": 1}) == {"a": 1}
    assert coerce_arguments("no json") == {}
    assert coerce_arguments(None) == {}


def test_extract_json_object() -> None:
    obj = extract_json_object('respuesta\n{"a": 1, "b": [1,2]}\nfin')
    assert obj == {"a": 1, "b": [1, 2]}
    assert extract_json_object("sin json") is None


def test_validate_arguments_ok() -> None:
    schemas = [
        ToolSchema(
            name="read_file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
    ]
    validate_arguments("read_file", {"path": "a.txt"}, schemas)  # no lanza


def test_validate_arguments_fail() -> None:
    schemas = [
        ToolSchema(
            name="read_file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
    ]
    with pytest.raises(ToolError, match="argumentos inválidos"):
        validate_arguments("read_file", {}, schemas)


def test_validate_arguments_no_schema_skips() -> None:
    validate_arguments("no_schema", {"cualquier": 1}, [])  # no lanza
