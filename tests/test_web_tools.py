"""Internet tools: validación de URLs, HTML, búsqueda y fetch (sin red real)."""

import asyncio
import socket
from pathlib import Path

import httpx
import pytest

from satellite.tools.base import ToolContext
from satellite.tools import web
from satellite.tools.web import WebDownloadTool, WebFetchTool, WebSearchTool


def run(coro):
    return asyncio.run(coro)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(project_root=tmp_path)


def test_validate_url_only_http_and_host() -> None:
    assert web._validate_url("https://example.com/a?b=1")[0] == "https://example.com/a?b=1"
    assert web._validate_url("ftp://example.com")[1]
    assert web._validate_url("https://")[1]
    assert web._validate_url("")[1]


def test_private_and_public_ips() -> None:
    assert web._ip_is_public("8.8.8.8") is True
    assert web._ip_is_public("127.0.0.1") is False
    assert web._ip_is_public("10.0.0.1") is False
    assert web._ip_is_public("192.168.1.10") is False
    assert web._ip_is_public("::1") is False


def test_html_to_text_skips_scripts_and_styles() -> None:
    html = (
        "<html><head><style>.x{}</style><script>alert(1)</script></head>"
        "<body><h1>Hola</h1><p>Mundo <b>bonito</b></p><ul><li>uno</li></ul></body></html>"
    )
    text = web._html_to_text(html)
    assert "Hola" in text and "Mundo bonito" in text and "uno" in text
    assert "alert" not in text and ".x{" not in text


def test_parse_search_results_unwraps_ddg_redirects() -> None:
    markup = """
    <div class="result">
      <a rel="nofollow" class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fx%3D1">
        Título <b>uno</b>
      </a>
      <a class="result__snippet">Fragmento <b>útil</b></a>
    </div>
    """
    results = web._parse_search_results(markup, max_results=5)
    assert results == [
        {
            "title": "Título uno",
            "url": "https://example.com/page?x=1",
            "snippet": "Fragmento útil",
        }
    ]


def test_search_tool_returns_structured_untrusted_results(tmp_path, monkeypatch) -> None:
    async def fake_search(query, max_results):
        return [
            {
                "title": "Resultado",
                "url": "https://example.com",
                "snippet": "texto",
            }
        ][:max_results]

    monkeypatch.setattr(web, "_search", fake_search)
    result = run(WebSearchTool().execute({"query": "algo"}, _ctx(tmp_path)))
    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["untrusted"] is True
    assert result.output["results"][0]["url"] == "https://example.com"


def test_fetch_tool_blocks_private_hosts(tmp_path) -> None:
    result = run(
        WebFetchTool().execute({"url": "http://127.0.0.1/secret"}, _ctx(tmp_path))
    )
    assert result.success is False
    assert result.error_code == "web_url_blocked"


def test_validate_url_rejects_invalid_ports(tmp_path) -> None:
    for url in (
        "http://example.com:99999",
        "http://example.com:abc",
        "http://example.com:0",
    ):
        normalized, error = web._validate_url(url)
        assert normalized == ""
        assert "puerto" in error.lower()

    public, reason = run(web._check_public_host("http://example.com:99999"))
    assert public is False
    assert "puerto" in reason.lower()

    result = run(
        WebFetchTool().execute(
            {"url": "http://example.com:99999"}, _ctx(tmp_path)
        )
    )
    assert result.success is False
    assert result.error_code == "invalid_arguments"


def test_fetch_pins_validated_ip_and_keeps_host_and_sni(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "example.com"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"ok"
        )

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(web.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(web.httpx, "AsyncClient", client_factory)

    page = run(web._fetch("https://example.com/page"))

    assert page["url"] == "https://example.com/page"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://93.184.216.34/page"
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"


def test_fetch_blocks_https_to_http_redirect(monkeypatch) -> None:
    requests: list[str] = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "http://example.com/next"},
            content=b"",
        )

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(web.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(web.httpx, "AsyncClient", client_factory)

    with pytest.raises(ValueError, match="HTTPS"):
        run(web._fetch("https://example.com/start"))

    assert requests == ["https://93.184.216.34/start"]


def test_download_tool_writes_binary_file(tmp_path, monkeypatch) -> None:
    async def fake_public(url):
        return True, ""

    async def fake_download(url, **kwargs):
        return {
            "url": url,
            "status": 200,
            "content_type": "application/pdf",
            "filename": "paper.pdf",
            "data": b"%PDF-1.4 contenido",
            "size": 18,
        }

    monkeypatch.setattr(web, "_check_public_host", fake_public)
    monkeypatch.setattr(web, "_download", fake_download)
    result = run(
        WebDownloadTool().execute(
            {"url": "https://example.com/paper.pdf"}, _ctx(tmp_path)
        )
    )
    assert result.success is True
    path = Path(result.output["path"])
    assert path == tmp_path / "downloads" / "paper.pdf"
    assert path.read_bytes() == b"%PDF-1.4 contenido"
    assert result.output["untrusted"] is True


def test_download_tool_refuses_overwrite_by_default(tmp_path, monkeypatch) -> None:
    async def fake_public(url):
        return True, ""

    async def fake_download(url, **kwargs):
        return {
            "url": url,
            "status": 200,
            "content_type": "text/plain",
            "filename": "a.txt",
            "data": b"nuevo",
            "size": 5,
        }

    monkeypatch.setattr(web, "_check_public_host", fake_public)
    monkeypatch.setattr(web, "_download", fake_download)
    target = tmp_path / "a.txt"
    target.write_text("viejo", encoding="utf-8")

    result = run(
        WebDownloadTool().execute(
            {"url": "https://example.com/a.txt", "path": "a.txt"}, _ctx(tmp_path)
        )
    )
    assert result.success is False
    assert result.error_code == "file_exists"
    assert target.read_text(encoding="utf-8") == "viejo"


def test_download_tool_blocks_private_hosts(tmp_path) -> None:
    result = run(
        WebDownloadTool().execute(
            {"url": "http://127.0.0.1/a.bin"}, _ctx(tmp_path)
        )
    )
    assert result.success is False
    assert result.error_code == "web_url_blocked"


def test_lite_parser_reads_duckduckgo_lite_markup() -> None:
    markup = (
        "<table><tr><td><a rel='nofollow' "
        "href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa' "
        "class='result-link'>Example A</a></td></tr>"
        "<tr><td class='result-snippet'>Snippet A</td></tr>"
        "<tr><td><a href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb' "
        "class='result-link'>Example B</a></td></tr>"
        "<tr><td class='result-snippet'>Snippet B</td></tr></table>"
    )
    results = web._parse_lite_results(markup, max_results=5)
    assert [item["title"] for item in results] == ["Example A", "Example B"]
    assert results[0]["url"] == "https://example.com/a"
    assert results[0]["snippet"] == "Snippet A"


def test_search_falls_back_to_lite_when_html_is_a_challenge(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        if "lite.duckduckgo.com" in url:
            return {
                "status": 200,
                "text": (
                    "<a href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fok' "
                    "class='result-link'>Fallback</a>"
                    "<td class='result-snippet'>Works</td>"
                ),
            }
        return {"status": 202, "text": "<html>challenge</html>"}

    monkeypatch.setattr(web, "_fetch", fake_fetch)
    results = run(web._search("some query", 3))
    assert results and results[0]["url"] == "https://example.com/ok"
    assert any("lite.duckduckgo.com" in url for url in calls)


def test_fetch_tool_wraps_text_and_drops_scripts(tmp_path, monkeypatch) -> None:
    async def fake_public(url):
        return True, ""

    async def fake_fetch(url, **kwargs):
        return {
            "url": url,
            "status": 200,
            "content_type": "text/html",
            "text": "<html><body><h1>Hello</h1><script>bad()</script></body></html>",
            "truncated": False,
            "textual": True,
        }

    monkeypatch.setattr(web, "_check_public_host", fake_public)
    monkeypatch.setattr(web, "_fetch", fake_fetch)
    result = run(
        WebFetchTool().execute({"url": "https://example.com"}, _ctx(tmp_path))
    )
    assert result.success is True
    content = result.output["content"]
    assert content.startswith('<data source="web_page"')
    assert "Hello" in content
    assert "bad()" not in content
    assert result.output["untrusted"] is True
