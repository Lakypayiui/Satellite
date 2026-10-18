"""Think switch + esfuerzo de razonamiento: capacidades, payload y persistencia.

Sin red: el payload se comprueba con un cliente OpenAI falso. Los valores de DeepSeek
salen de su API oficial documentada (``thinking: {type: enabled|disabled}`` y
``reasoning_effort: none|low|high|max``); los de OpenAI, de ``reasoning_effort``.
"""

import asyncio
import pytest

from satellite.config import providers as pconfig


@pytest.fixture(autouse=True)
def isolate_global_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "global"))
from satellite.core.messages import LLMRequest, Message, Role
from satellite.providers.capabilities import (
    effort_metadata,
    effort_options,
    resolve_effort,
    resolve_think,
)
from satellite.providers.factory import ProviderConfig
from satellite.providers.openai_compat import OpenAICompatProvider


class FakeOpenAIResponse:
    def __init__(self, content="", tool_calls=None, finish_reason="stop", usage=None):
        self.choices = [_Choice(content, tool_calls, finish_reason)]
        self.usage = usage


class _Choice:
    def __init__(self, content, tool_calls, finish_reason):
        self.message = _Message(content, tool_calls)
        self.finish_reason = finish_reason


class _Message:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []
        self.response = None

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def run(coro):
    return asyncio.run(coro)


def make_request(**overrides) -> LLMRequest:
    messages = (Message(role=Role.USER, content="hola"),)
    kwargs = {"messages": messages}
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def _sent(**cfg_kwargs) -> dict:
    client = FakeOpenAIClient()
    client.response = FakeOpenAIResponse(content="ok")
    provider = OpenAICompatProvider(
        ProviderConfig(name="deepseek", model="deepseek-flash", **cfg_kwargs), client=client
    )
    run(provider.complete(make_request()))
    return client.calls[-1]


# --- capacidades declaradas --------------------------------------------------


def test_deepseek_flash_declares_documented_efforts_and_think() -> None:
    # Docs oficiales DeepSeek: reasoning_effort none|low|high|max y thinking enabled|disabled.
    assert effort_options("deepseek-flash") == ("auto", "off", "low", "high", "max")
    meta = effort_metadata("deepseek-flash")
    assert meta["param"] == "reasoning_effort"
    assert meta["think_param"] == "thinking"
    assert meta["think_on"] == "enabled"
    assert meta["think_off"] == "disabled"
    assert meta["can_disable"] is True
    assert meta["reasoning"] is True


def test_model_without_reasoning_has_no_think_channel() -> None:
    assert effort_options("gpt-4o-mini") == ()
    assert resolve_think("gpt-4o-mini", None, True) is None
    assert resolve_think("gpt-4o-mini", None, False) is None


def test_gpt_6_luna_effort_levels_are_available_without_a_think_channel() -> None:
    assert effort_options("gpt-6-luna") == (
        "auto", "off", "low", "medium", "high", "xhigh", "max"
    )
    meta = effort_metadata("gpt-6-luna")
    assert meta["param"] == "reasoning_effort"
    assert meta["think_param"] == ""
    assert resolve_think("gpt-6-luna", None, True) is None


def test_explicit_override_declares_think_for_a_custom_model() -> None:
    overrides = {
        "mi-modelo-raro": {
            "effort_levels": ["low", "high"],
            "effort_control": "reasoning_effort",
            "think_param": "thinking",
            "think_on": "enabled",
            "think_off": "disabled",
        }
    }
    assert effort_options("mi-modelo-raro", overrides) == ("auto", "off", "low", "high")
    assert resolve_think("mi-modelo-raro", overrides, True) == {"thinking": {"type": "enabled"}}
    assert resolve_think("mi-modelo-raro", overrides, False) == {"thinking": {"type": "disabled"}}


# --- payload real ------------------------------------------------------------


def test_think_on_sends_thinking_enabled_and_effort() -> None:
    sent = _sent(reasoning_effort="high", thinking=True)
    assert sent["reasoning_effort"] == "high"
    assert sent["extra_body"]["thinking"] == {"type": "enabled"}


def test_think_off_sends_thinking_disabled_without_a_fake_effort() -> None:
    sent = _sent(reasoning_effort="high", thinking=False)
    assert sent["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in sent


def test_auto_sends_no_parameters_and_unknown_effort_is_dropped() -> None:
    sent = _sent(reasoning_effort="auto", thinking=True)
    assert "reasoning_effort" not in sent
    # Think encendido sí viaja explícitamente (el modelo lo declara).
    assert sent["extra_body"]["thinking"] == {"type": "enabled"}

    sent = _sent(reasoning_effort="inventado", thinking=None)
    assert "reasoning_effort" not in sent
    assert "extra_body" not in sent


def test_effort_off_uses_the_declared_off_value() -> None:
    assert resolve_effort("deepseek-flash", None, "auto", thinking=True) is None
    assert resolve_effort("deepseek-flash", None, "off", thinking=True) == "none"
    # Con el canal Think apagado no se añade un esfuerzo de más.
    assert resolve_effort("deepseek-flash", None, "high", thinking=False) is None


def test_non_reasoning_model_sends_nothing_extra() -> None:
    client = FakeOpenAIClient()
    client.response = FakeOpenAIResponse(content="ok")
    provider = OpenAICompatProvider(
        ProviderConfig(name="openai", model="gpt-4o-mini", reasoning_effort="high", thinking=True),
        client=client,
    )
    run(provider.complete(make_request()))
    sent = client.calls[-1]
    assert "reasoning_effort" not in sent
    assert "extra_body" not in sent


# --- persistencia por proveedor+modelo --------------------------------------


def test_provider_think_persists_per_provider_and_model(tmp_path) -> None:
    saved = pconfig.save_provider(
        tmp_path,
        name="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com/v1",
        reasoning_effort="high",
        think=True,
    )
    assert saved.think is True
    data = pconfig.runtime_config(tmp_path)
    entry = data["providers"][0]
    assert entry["think"] is True
    assert entry["reasoning_effort"] == "high"
    assert data["llm"]["think"] is True

    pconfig.update_provider(tmp_path, saved.id, think=False)
    assert pconfig.find_provider(tmp_path, saved.id).think is False
    assert pconfig.load_config(tmp_path)["llm"]["think"] is False


def test_saved_think_reaches_the_provider_config(tmp_path) -> None:
    from satellite.cli.runtime_builder import _provider_config_from

    pconfig.save_provider(
        tmp_path,
        name="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com/v1",
        reasoning_effort="max",
        think=True,
    )
    # El camino global exige los campos de nivel superior (como el config real).
    data = pconfig.runtime_config(tmp_path)

    cfg = _provider_config_from(data)
    assert cfg is not None
    assert cfg.thinking is True
    assert cfg.reasoning_effort == "max"


def test_role_think_is_stored_and_propagated(tmp_path) -> None:
    from satellite.cli.runtime_builder import _provider_config_from

    pconfig.save_provider(
        tmp_path,
        name="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com/v1",
    )
    saved = pconfig.list_providers(tmp_path)[0]
    pconfig.set_roles(
        tmp_path,
        {"orchestrator": {"provider": saved.id, "think": False, "reasoning_effort": "high"}},
    )
    data = pconfig.runtime_config(tmp_path)
    assert data["llm"]["orchestrator"]["think"] is False
    cfg = _provider_config_from(data, role="orchestrator")
    assert cfg is not None
    assert cfg.thinking is False
    assert cfg.reasoning_effort == "high"
