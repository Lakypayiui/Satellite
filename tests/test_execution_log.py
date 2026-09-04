"""Tests for the execution logger (satellite_py/execution_log.py)."""

import json

from satellite_py.execution_log import ExecutionLogger, ExecutionRecord


def test_log_writes_neutral_json(tmp_path):
    logger = ExecutionLogger(str(tmp_path))
    record = ExecutionRecord(
        agent_id=1,
        agent_name="sum",
        input={"a": 1, "b": 2},
        output={"result": 3},
        status="SUCCESS",
        relevance_score=0.9,
    )
    exec_id = logger.log(record)
    assert exec_id
    assert logger.count() == 1
    files = list(tmp_path.glob("exec_*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["agent_id"] == 1
    assert doc["agent_name"] == "sum"
    assert doc["output"] == {"result": 3}
    assert doc["status"] == "SUCCESS"
    assert doc["relevance_score"] == 0.9


def test_log_uses_provided_execution_id(tmp_path):
    logger = ExecutionLogger(str(tmp_path))
    logger.log(ExecutionRecord(execution_id="abc123", agent_id=2, status="FAILED"))
    assert (tmp_path / "exec_abc123.json").is_file()
