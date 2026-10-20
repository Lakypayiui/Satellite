"""Internet tools: validación de URLs, HTML, búsqueda y fetch (sin red real)."""

import asyncio
from pathlib import Path

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
