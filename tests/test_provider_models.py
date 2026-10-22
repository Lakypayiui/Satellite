"""Listado de modelos (sin red: cliente HTTP simulado)."""

import json

import httpx
import pytest

from satellite.config import model_sources, models


def patch_http(
    monkeypatch,
    *,
    payload="{}",
    status=200,
    error=None,
    capture=None,
    headers=None,
    client_kwargs=None,
):
    def handler(request):
        if capture is not None:
            capture.append(request)
        if error is not None:
            raise error
        return httpx.Response(status, text=payload, headers=headers, request=request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client(*args, **kwargs):
        if client_kwargs is not None:
            client_kwargs.append(kwargs)
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("satellite.config.models.httpx.Client", client)


def test_models_url_openai_variants() -> None:
    assert models.models_url("https://api.groq.com/openai/v1") == "https://api.groq.com/openai/v1/models"
    assert models.models_url("https://api.groq.com/openai/v1/") == "https://api.groq.com/openai/v1/models"
    assert models.models_url("https://x/v1/models") == "https://x/v1/models"
    assert models.models_url("") == "https://api.openai.com/v1/models"


def test_models_url_anthropic_does_not_duplicate_v1() -> None:
    assert models.models_url("https://api.anthropic.com", "anthropic") == "https://api.anthropic.com/v1/models"
    assert models.models_url("https://api.anthropic.com/v1", "anthropic") == "https://api.anthropic.com/v1/models"
    assert models.models_url("", "anthropic") == "https://api.anthropic.com/v1/models"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "data:text/plain,secreto", "ftp://example.com/models"],
)
def test_fetch_models_rejects_non_http_schemes(monkeypatch, url) -> None:
    captured: list = []
    patch_http(monkeypatch, capture=captured)

    result = models.fetch_models(url)

    assert result.ok is False
    assert "http" in result.error
    assert captured == []


def test_fetch_models_uses_timeout_and_disables_redirects(monkeypatch) -> None:
    seen: list = []
    patch_http(monkeypatch, payload='{"data": []}', client_kwargs=seen)

    models.fetch_models("https://x/v1", timeout=2.5)

    assert seen[0]["timeout"] == 2.5
    assert seen[0]["follow_redirects"] is False


def test_fetch_models_does_not_follow_redirects(monkeypatch) -> None:
    captured: list = []
    patch_http(
        monkeypatch,
        status=302,
        headers={"Location": "https://evil.example/models"},
        capture=captured,
    )

    result = models.fetch_models("https://x/v1")

    assert len(captured) == 1
    assert result.ok is False


COMMANDCODE_MODELS_URL = "https://api.commandcode.ai/provider/v1/models?view=client"


def test_commandcode_model_catalog_uses_shared_public_models_endpoint(monkeypatch) -> None:
    captured: list = []
    patch_http(monkeypatch, payload='{"data": []}', capture=captured)
    openai_url = models.models_url(
        "https://api.commandcode.ai/provider/v1", "openai", "commandcode"
    )
    anthropic_url = models.models_url(
        "https://api.commandcode.ai/provider", "anthropic", "commandcode-anthropic"
    )
    # Un solo endpoint (la vista de cliente) sirve a las dos familias.
    assert openai_url == COMMANDCODE_MODELS_URL
    assert anthropic_url == openai_url
    models.fetch_models(
        "https://api.commandcode.ai/provider/v1",
        "openai",
        "cmd-key",
        provider="commandcode",
    )
    request = captured[0]
    assert str(request.url) == COMMANDCODE_MODELS_URL
    headers = {k.lower(): v for k, v in request.headers.items()}
    # El edge rechaza con 403 el User-Agent por defecto de urllib: hay que identificarse.
    assert headers.get("user-agent") == model_sources.COMMANDCODE_USER_AGENT
    assert "authorization" not in headers


def test_commandcode_public_catalog_omits_auth_header_without_a_key(monkeypatch) -> None:
    captured: list = []
    patch_http(monkeypatch, payload='{"data": []}', capture=captured)
    models.fetch_models(
        "https://api.commandcode.ai/provider/v1",
        "openai",
        provider="commandcode",
    )
    headers = {k.lower(): v for k, v in captured[0].headers.items()}
    assert "authorization" not in headers
    assert headers.get("user-agent") == model_sources.COMMANDCODE_USER_AGENT


def test_commandcode_anthropic_catalog_also_omits_auth_header(monkeypatch) -> None:
    captured: list = []
    patch_http(monkeypatch, payload='{"data": []}', capture=captured)
    models.fetch_models(
        "https://api.commandcode.ai/provider",
        "anthropic",
        "cmd-key",
        provider="commandcode-anthropic",
    )
    request = captured[0]
    assert str(request.url) == COMMANDCODE_MODELS_URL
    headers = {k.lower(): v for k, v in request.headers.items()}
    assert "authorization" not in headers
    assert "x-api-key" not in headers
    assert headers.get("user-agent") == model_sources.COMMANDCODE_USER_AGENT


def test_commandcode_client_catalog_parses_both_families(monkeypatch) -> None:
    payload = json.dumps(
        {
            "object": "list",
            "catalogVersion": "abc",
            "data": [{"id": "claude-sonnet-5-5"}, {"id": "gpt-5"}],
        }
    )
    patch_http(monkeypatch, payload=payload)
    for base_url, family, key in (
        ("https://api.commandcode.ai/provider/v1", "openai", "commandcode"),
        ("https://api.commandcode.ai/provider", "anthropic", "commandcode-anthropic"),
    ):
        result = models.fetch_models(base_url, family, "cmd-key", provider=key)
        assert result.ok is True
        assert result.models == ("claude-sonnet-5-5", "gpt-5")
        assert result.url == COMMANDCODE_MODELS_URL


def test_fetch_models_parses_data_field(monkeypatch) -> None:
    patch_http(monkeypatch, payload=json.dumps({"data": [{"id": "b"}, {"id": "a"}]}))
    result = models.fetch_models("https://x/v1")
    assert result.ok is True
    assert result.models == ("a", "b")
    assert result.url.endswith("/v1/models")


def test_fetch_models_parses_models_field_and_dedupes(monkeypatch) -> None:
    patch_http(monkeypatch, payload=json.dumps({"models": [{"id": "m"}, {"id": "m"}]}))
    result = models.fetch_models("https://x/v1")
    assert result.models == ("m",)


def test_fetch_models_sends_bearer_for_openai(monkeypatch) -> None:
    captured: list = []
    patch_http(monkeypatch, payload='{"data": []}', capture=captured)
    models.fetch_models("https://x/v1", "openai", "sk-abc")
    headers = {k.lower(): v for k, v in captured[0].headers.items()}
    assert headers.get("authorization") == "Bearer sk-abc"


def test_fetch_models_uses_bearer_not_needed_without_key(monkeypatch) -> None:
    captured: list = []
    patch_http(monkeypatch, payload='{"data": []}', capture=captured)
    models.fetch_models("http://localhost:8080/v1")
    headers = {k.lower(): v for k, v in captured[0].headers.items()}
    assert headers.get("authorization") == "Bearer not-needed"


def test_fetch_models_anthropic_uses_x_api_key(monkeypatch) -> None:
    captured: list = []
    patch_http(monkeypatch, payload='{"data": []}', capture=captured)
    models.fetch_models("https://api.anthropic.com", "anthropic", "sk-ant")
    headers = {k.lower(): v for k, v in captured[0].headers.items()}
    assert headers.get("x-api-key") == "sk-ant"
    assert "anthropic-version" in headers


def test_fetch_models_reports_http_error(monkeypatch) -> None:
    patch_http(monkeypatch, status=401, payload="")
    result = models.fetch_models("https://x/v1", "openai", "bad")
    assert result.ok is False
    assert "401" in result.error
    assert result.url.endswith("/models")


def test_fetch_models_preserves_structured_http_error_detail(monkeypatch) -> None:
    body = json.dumps({"error": {"code": "upgrade_required", "message": "Plan does not allow this endpoint"}})
    patch_http(monkeypatch, status=403, payload=body)
    result = models.fetch_models("https://x/v1", "openai", "bad")
    assert result.auth_error is True
    assert "upgrade_required" in result.error
    assert "Plan does not allow this endpoint" in result.error


def test_fetch_models_reports_connection_error(monkeypatch) -> None:
    patch_http(monkeypatch, error=httpx.ConnectError("connection refused"))
    result = models.fetch_models("http://localhost:8080/v1")
    assert result.ok is False
    assert "connection refused" in result.error


def test_fetch_models_reports_non_json(monkeypatch) -> None:
    patch_http(monkeypatch, payload="<html>nope</html>")
    result = models.fetch_models("https://x/v1")
    assert result.ok is False
    assert "no-JSON" in result.error


def test_fetch_models_ok_but_empty_is_loud(monkeypatch) -> None:
    patch_http(monkeypatch, payload='{"data": []}')
    result = models.fetch_models("https://x/v1")
    assert result.ok is True
    assert result.models == ()
    assert "no traía modelos" in result.error


def test_context_for_reads_the_declared_window() -> None:
    """La ventana declarada sale de claves de contexto reales."""
    result = models.ModelsResult(
        ok=True,
        models=("m1", "m2", "m3"),
        details={
            "m1": {"id": "m1", "max_input_tokens": 200_000, "max_tokens": 8192},
            "m2": {"id": "m2", "max_tokens": 8192},  # solo el tope de salida
            "m3": {"id": "m3", "meta": {"n_ctx": 32_768}},
        },
    )
    assert result.context_for("m1") == 200_000  # gana la ventana, no el tope de salida
    assert result.context_for("m2") is None  # `max_tokens` NO es la ventana
    assert result.context_for("m3") == 32_768  # claves anidadas (p. ej. LM Studio)


def test_default_model() -> None:
    assert models.default_model(("a", "b")) == "a"
    assert models.default_model(()) == ""
