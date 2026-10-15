"""Configuración LLM (F6): max_tokens por rol e interfaz de Batch API."""

import json
from pathlib import Path

import pytest

from satellite.core.errors import ProviderError
from satellite.core.messages import CallContext, LLMRequest, Message, Role
from satellite.kernel.policy import Quotas, Settings, load_settings
from satellite.providers.batch import (
    BATCH_ENABLED,
    BatchCapableProvider,
    is_batch_capable,
    submit_batch,
)
from satellite.providers.fake import FakeLLM
from satellite.providers.metered import MeteredProvider


def make_request(role: str = "agent", max_tokens: int | None = None) -> LLMRequest:
    return LLMRequest(
        messages=(Message(role=Role.USER, content="hola"),),
        max_tokens=max_tokens,
        call=CallContext(role=role),
    )


# ---------------------------------------------------------------- max_tokens


def test_settings_max_tokens_from_dict_and_scalar(tmp_path: Path) -> None:
    by_role = tmp_path / "a.json"
    by_role.write_text(json.dumps({"llm": {"max_tokens": {"creator": 512, "*": 64}}}), encoding="utf-8")
    settings = load_settings(by_role)
    assert settings.max_tokens_for("creator") == 512
    assert settings.max_tokens_for("agents") == 64

    scalar = tmp_path / "b.json"
    scalar.write_text(json.dumps({"max_tokens": 256}), encoding="utf-8")
    assert load_settings(scalar).max_tokens_for("lo-que-sea") == 256

    assert load_settings(tmp_path / "nope.json").max_tokens_for("x") is None
    assert Settings().max_tokens_for("x") is None
    assert Settings(max_tokens={"creator": 0}).max_tokens_for("creator") is None


def test_max_tokens_ignores_garbage_values(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps({"llm": {"max_tokens": {"creator": "no-numero", "agents": True}}}),
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.max_tokens_for("creator") is None
    assert settings.max_tokens_for("agents") is None


def test_metered_provider_applies_role_limit_only_when_missing() -> None:
    inner = FakeLLM()
    provider = MeteredProvider(inner, None, max_tokens_by_role={"orchestrator": 1024})
    import asyncio

    asyncio.run(provider.complete(make_request(role="orchestrator")))
    asyncio.run(provider.complete(make_request(role="orchestrator", max_tokens=7)))
    asyncio.run(provider.complete(make_request(role="")))
    assert inner.requests[0].max_tokens == 1024
    assert inner.requests[1].max_tokens == 7
    assert inner.requests[2].max_tokens is None  # sin rol no hay límite que aplicar

def test_settings_to_dict_includes_max_tokens() -> None:
    settings = Settings(quotas=Quotas(), max_tokens={"creator": 100})
    assert settings.to_dict()["max_tokens"] == {"creator": 100}


# ---------------------------------------------------------------- batch


def test_batch_api_is_documented_but_not_implemented() -> None:
    assert BATCH_ENABLED is False
    provider = BatchCapableProvider()
    assert provider.supports_batch is False
    import asyncio

    with pytest.raises(NotImplementedError):
        asyncio.run(provider.submit_batch([make_request(role="agent")]))
    with pytest.raises(NotImplementedError):
        asyncio.run(provider.fetch_batch("b1"))


def test_is_batch_capable_checks_the_wrapper_chain() -> None:
    plain = FakeLLM()
    assert is_batch_capable(plain) is False

    class WithBatch:
        supports_batch = True

    assert is_batch_capable(WithBatch()) is True
    # el metered provider delega en lo que envuelve
    assert is_batch_capable(MeteredProvider(WithBatch(), None)) is True
    assert is_batch_capable(MeteredProvider(plain, None)) is False


def test_submit_batch_without_support_is_loud() -> None:
    with pytest.raises(ProviderError):
        submit_batch(FakeLLM(), [make_request(role="agent")])
