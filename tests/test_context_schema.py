"""Tests for the neutral context schema (E6 guarantee, context_schema.py)."""

from satellite_py.context_schema import (
    ContextResult,
    NeedInfo,
    last_session_path,
    load_session,
    persist_session,
    sanitize_session,
)


def test_need_info_and_result_are_plain_neutral():
    needs = NeedInfo(category="x", files_needed=["a.py"], sufficient=True)
    result = ContextResult(refined_prompt="p", needs_user_input=False)
    assert needs.category == "x"
    assert result.refined_prompt == "p"


def test_sanitize_drops_provider_fields_at_root():
    doc = {
        "schema": "satellite/context-session/1",
        "goal": "g",
        "provider_agnostic": True,
        "model_used": "gpt-4o",  # campo de provider: debe caer
        "raw_prompt_sent": "..." ,  # memoria del modelo: debe caer
        "steps": [],
    }
    cleaned = sanitize_session(doc)
    assert cleaned["goal"] == "g"
    assert "model_used" not in cleaned
    assert "raw_prompt_sent" not in cleaned


def test_sanitize_keeps_agent_outputs_but_limits_compressed():
    doc = {
        "schema": "satellite/context-session/1",
        "steps": [
            {
                "index": 0,
                "agent_name": "sum",
                "status": "SUCCESS",
                "output": {"result": 5},
                "provider_note": "x",  # debe caer del step
            }
        ],
        "final_context": {
            "intention": "sumar",
            "constraints": [],
            "references": {"a.py": "sum"},
            "status": "hecho",
            "provider_logprobs": 0.9,  # debe caer del doc comprimido
        },
    }
    cleaned = sanitize_session(doc)
    assert cleaned["steps"][0]["output"] == {"result": 5}
    assert "provider_note" not in cleaned["steps"][0]
    assert cleaned["final_context"]["intention"] == "sumar"
    assert "provider_logprobs" not in cleaned["final_context"]


def test_persist_and_load_roundtrip(tmp_path):
    session_file = tmp_path / "session_x.json"
    doc = {"schema": "satellite/context-session/1", "goal": "g", "provider_agnostic": True, "steps": [], "junk_provider": 1}
    persist_session(str(session_file), doc)
    loaded = load_session(str(session_file))
    assert loaded["goal"] == "g"
    assert "junk_provider" not in loaded


def test_load_session_missing_returns_empty(tmp_path):
    assert load_session(str(tmp_path / "no.json")) == {}


def test_last_session_path_returns_most_recent(tmp_path):
    (tmp_path / "session_1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "session_2.json").write_text("{}", encoding="utf-8")
    latest = last_session_path(str(tmp_path))
    assert latest.endswith("session_2.json")
    assert last_session_path(str(tmp_path / "empty_dir")) == ""
