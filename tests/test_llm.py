"""Tests for the LLM provider factory (satellite_py/llm.py)."""

import json

from satellite_py.llm import AnthropicClient, LLMConfig, OpenAICompatibleClient, load_llm_config


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=500):
        self.calls.append((system_prompt, user_prompt, max_tokens))
        return self.responses.pop(0)


def test_llm_config_openai_wire_defaults():
    config = LLMConfig(provider="openai", model="m", api_key="k")
    assert config.provider == "openai"


def test_load_llm_config_from_config_json_local(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "use_local_llm": True,
                "local_llm": {"port": 8080, "model": "gemma-local", "max_rounds": 5},
                "llm": {"provider": "deepseek"},
            }
        ),
        encoding="utf-8",
    )
    resolved = load_llm_config(config_path)
    assert resolved.provider == "local"
    assert resolved.model == "gemma-local"
    assert resolved.base_url == "http://localhost:8080"
    client = resolved.create_client()
    assert isinstance(client, OpenAICompatibleClient)


def test_load_llm_config_external_openai(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_key": "sk-test",
                    "base_url": "https://api.openai.com/v1",
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = load_llm_config(config_path)
    assert resolved.provider == "openai"
    assert resolved.model == "gpt-4o-mini"
    assert resolved.api_key == "sk-test"
    assert isinstance(resolved.create_client(), OpenAICompatibleClient)


def test_load_llm_config_openai_compatible_preserves_provider(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "openai-compatible",
                    "model": "llama-3-8b",
                    "api_key": "sk-custom",
                    "base_url": "https://mi-api.example.com/v1",
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = load_llm_config(config_path)
    assert resolved.provider == "openai-compatible"
    assert resolved.base_url == "https://mi-api.example.com/v1"
    assert isinstance(resolved.create_client(), OpenAICompatibleClient)


def test_load_llm_config_anthropic(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"llm": {"provider": "anthropic", "model": "claude-x", "api_key": "ak-test"}}),
        encoding="utf-8",
    )
    resolved = load_llm_config(config_path)
    assert resolved.provider == "anthropic"
    assert isinstance(resolved.create_client(), AnthropicClient)


def test_load_llm_config_missing_file_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SATELLITE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    config_path = tmp_path / "no-config.json"
    resolved = load_llm_config(config_path)
    assert resolved.provider == "openai"
    assert resolved.model == "env-model"


def test_llm_role_config_propagates_api_key_from_role_to_others():
    """Una api_key puesta en un rol se reutiliza en los demás roles."""
    from satellite_py.llm import llm_role_config

    data = {
        "llm": {
            "provider": "openai-compatible",
            "model": "nvidia/nemotron",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "context": {"provider": "openai-compatible", "model": "nvidia/nemotron", "api_key": "nvapi-xyz"},
            "orchestrator": {"provider": "openai-compatible", "model": "nvidia/nemotron"},
        }
    }
    ctx = llm_role_config(data, "context")
    orch = llm_role_config(data, "orchestrator")
    agents = llm_role_config(data, "agents")
    # la key definida en context llega a orchestrador y agents.
    assert ctx.api_key == "nvapi-xyz"
    assert orch.api_key == "nvapi-xyz"
    assert agents.api_key == "nvapi-xyz"
    assert ctx.base_url == "https://integrate.api.nvidia.com/v1"
    assert orch.provider == "openai-compatible"
