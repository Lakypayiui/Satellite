"""El script de evaluación del flujo piloto funciona sin red (docs/PLAN-flujo-piloto.md, fase 7)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.providers.fake import FakeLLM

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_flow.py"


def load_script():
    spec = importlib.util.spec_from_file_location("eval_flow", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_runs_a_case_on_a_copy_and_checks_expectations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("print('x')\n", encoding="utf-8")
    (source / ".satellite" / "config").mkdir(parents=True)
    (source / ".satellite" / "config" / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
    )
    provider = FakeLLM(
        script=[
            {"tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "main.py"}}]},
            {"content": "main.py imprime x."},
        ],
        default_content="",
    )
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: FakeLLM(default_content="r") if role == "context" else default,
    )
    module = load_script()
    copy = module.copy_project(source)
    assert copy != source
    assert (copy / ".satellite" / "config" / "config.json").exists()

    result = asyncio.run(
        module.run_case(
            copy, {"id": "c", "turns": ["¿qué hace main.py?"], "expect": {"tools_any": ["read_file"]}}
        )
    )

    assert result["checks"] == {"reads_before_answering": True, "no_failures": True}
    turn = result["turns"][0]
    assert turn["pilot_tools"] == ["read_file"]
    assert turn["first_text_s"] is not None
    # La copia es la que se toca; el original no se modifica.
    assert json.loads((source / ".satellite" / "config" / "config.json").read_text()) == {
        "name": "fake",
        "model": "m",
    }


def test_tool_summary_counts_delegated_tools() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "eval_flow.py"
    spec = importlib.util.spec_from_file_location("eval_flow_summary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = [
        {"turns": [{"agent_tools": [
            {"agent": "python_coder", "tool": "read_file", "args": "", "ok": True},
            {"agent": "python_coder", "tool": "read_file", "args": "", "ok": True},
            {"agent": "python_coder", "tool": "code_task", "args": "", "ok": False},
        ]}]}
    ]
    assert module.tool_summary(results) == {"python_coder": {"read_file": 2, "code_task": 1}}
    assert module._short_args({"path": "a.py", "start_line": 3, "other": "x"}) == "path=a.py start_line=3"
