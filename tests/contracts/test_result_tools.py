"""Tests de contrato de resultados y modelos de tools."""

from satellite.core.result import (
    AgentResult,
    AgentStatus,
    ExecutionMetrics,
    Finding,
    FindingSeverity,
    ToolRecord,
)
from satellite.core.tool_models import ToolCall, ToolResult, ToolSchema


def test_agent_result_round_trip() -> None:
    result = AgentResult(
        node_id="n1",
        spec_id="html_writer",
        status=AgentStatus.OK,
        summary="HTML creado",
        output={"path": "index.html"},
        tool_calls=(
            ToolRecord(tool_name="write_file", arguments={"path": "index.html"}),
        ),
        findings=(
            Finding(severity=FindingSeverity.WARNING, message="sin doctype",
                    location="index.html:1", suggestion="agregar doctype"),
        ),
        metrics=ExecutionMetrics(tokens_in=100, tokens_out=50, duration_ms=30, retries=1),
        error=None,
    )
    restored = AgentResult.from_dict(result.to_dict())
    assert restored == result
    assert restored.tool_calls[0].tool_name == "write_file"
    assert restored.findings[0].severity is FindingSeverity.WARNING
    assert restored.metrics.retries == 1


def test_finding_round_trip_severity_string() -> None:
    d = {"severity": "error", "message": "falló", "location": "a:1", "suggestion": "reintenta"}
    f = Finding.from_dict(d)
    assert f.severity is FindingSeverity.ERROR
    assert Finding.from_dict(f.to_dict()) == f


def test_finding_severity_enum_constructor() -> None:
    f = Finding(severity=FindingSeverity.INFO, message="detalle")
    assert f.severity is FindingSeverity.INFO
    assert f.to_dict()["severity"] == "info"


def test_tool_call_round_trip() -> None:
    call = ToolCall(id="call-1", name="read_file", arguments={"path": "/x"})
    restored = ToolCall.from_dict(call.to_dict())
    assert restored == call


def test_tool_result_round_trip_arbitrary_output() -> None:
    for output in ("texto", {"a": 1}, None, [1, 2, 3]):
        tr = ToolResult(call_id="c1", success=True, output=output, duration_ms=5)
        assert ToolResult.from_dict(tr.to_dict()) == tr


def test_tool_schema_round_trip() -> None:
    schema = ToolSchema(name="read_file", description="lee", parameters={"type": "object"})
    assert ToolSchema.from_dict(schema.to_dict()) == schema
