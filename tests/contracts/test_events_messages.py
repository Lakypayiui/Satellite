"""Tests de contrato de eventos y mensajes LLM."""

import pytest

from satellite.core.events import EventType, ExecutionEvent
from satellite.core.messages import LLMRequest, LLMResponse, Message, Role, Usage
from satellite.core.tool_models import ToolCall
from satellite.core.errors import ContractError


def test_event_types_cover_nine() -> None:
    # Los 9 eventos base de Etapa 0 siguen presentes; los tipos de propuestas,
    # skills, delegación y gobernanza se añaden de forma aditiva.
    base_nine = {
        "task_started",
        "graph_built",
        "node_started",
        "node_finished",
        "tool_called",
        "validation_failed",
        "retry",
        "node_stuck",
        "task_finished",
    }
    assert base_nine <= {e.value for e in EventType}
    assert len(list(EventType)) == len({e.value for e in EventType})


def test_execution_event_round_trip() -> None:
    event = ExecutionEvent(
        event_type=EventType.NODE_FINISHED,
        task_id="t1",
        node_id="n2",
        tool_name="write_file",
        payload={"status": "ok"},
    )
    restored = ExecutionEvent.from_dict(event.to_dict())
    assert restored == event
    assert restored.event_type is EventType.NODE_FINISHED
    assert restored.node_id == "n2"
    assert restored.payload["status"] == "ok"


def test_message_roles() -> None:
    assert Message(role=Role.SYSTEM, content="sys").role is Role.SYSTEM
    assert Message(role=Role.USER, content="hola").role is Role.USER
    assert Message(role=Role.ASSISTANT, content="resp").role is Role.ASSISTANT
    assert Message(role=Role.TOOL, content="tool out", tool_call_id="c1").role is Role.TOOL


def test_message_tool_requires_call_id() -> None:
    with pytest.raises(ContractError):
        Message(role=Role.TOOL, content="sin id")


def test_assistant_with_tool_calls_no_content_ok() -> None:
    msg = Message(role=Role.ASSISTANT, tool_calls=(ToolCall(id="c1", name="read_file"),))
    assert msg.content == ""


def test_llm_request_empty_messages_raises() -> None:
    with pytest.raises(ContractError):
        LLMRequest(messages=())


def test_llm_request_valid_messages_ok() -> None:
    req = LLMRequest(messages=(Message(role=Role.USER, content="hola"),))
    assert len(req.messages) == 1


def test_llm_response_round_trip_with_tool_calls() -> None:
    resp = LLMResponse(
        content="llamando",
        tool_calls=(ToolCall(id="tc1", name="write_file", arguments={"path": "x"}),),
        stop_reason="tool_calls",
        usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )
    restored = LLMResponse.from_dict(resp.to_dict())
    assert restored == resp
    assert restored.tool_calls[0].name == "write_file"
    assert restored.usage.total_tokens == 30
