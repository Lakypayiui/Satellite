import pytest

from satellite_py.runtime import agent_host_bridge, factory_bridge


def test_agent_bridge_sends_json(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs["input"]
        return type("Completed", (), {"returncode": 0, "stdout": '{"status":"SUCCESS"}', "stderr": ""})()

    monkeypatch.setattr(agent_host_bridge.subprocess, "run", fake_run)
    assert agent_host_bridge.run_agent("agent.dll", {"agent_id": 1})["status"] == "SUCCESS"
    assert captured["args"] == ["./build/satellite_agent_host", "agent.dll"]
    assert captured["input"].endswith("\n")


def test_agent_bridge_reports_process_failure(monkeypatch):
    monkeypatch.setattr(agent_host_bridge.subprocess, "run", lambda *args, **kwargs: type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "boom"})())
    with pytest.raises(RuntimeError, match="boom"):
        agent_host_bridge.run_agent("agent.dll", {})


def test_factory_bridge_sends_goal_and_capability(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return type("Completed", (), {"returncode": 0, "stdout": '{"ok":true}', "stderr": ""})()

    monkeypatch.setattr(factory_bridge.subprocess, "run", fake_run)
    assert factory_bridge.create_agent("build sum", "math.sum")["ok"]
    assert captured["args"] == ["./build/satellite_factory_cli"]


def test_bridge_rejects_empty_json(monkeypatch):
    monkeypatch.setattr(factory_bridge.subprocess, "run", lambda *args, **kwargs: type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    with pytest.raises(RuntimeError, match="empty stdout"):
        factory_bridge.create_agent("goal", "capability")
