"""Streaming de proveedores y publicación de deltas (docs/PLAN-flujo-piloto.md, fase 1)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from satellite.core.errors import ProviderError
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.messages import LLMRequest, LLMResponse, Message, Role, StreamChunk
from satellite.desktop.events import EventBridge
from satellite.providers.anthropic_provider import AnthropicProvider
from satellite.providers.base import LLMProvider
from satellite.providers.factory import ProviderConfig
from satellite.providers.fake import FakeLLM
from satellite.providers.metered import MeteredProvider
from satellite.providers.openai_compat import OpenAICompatProvider
from satellite.providers.streaming import DeltaPublisher, complete_streaming
from satellite.safe_proxy.interceptor import StreamRestorer
from satellite.safe_proxy.sanitizer import Vault


def run(coro):
    return asyncio.run(coro)


def request() -> LLMRequest:
    return LLMRequest(messages=(Message(role=Role.USER, content="hola"),))


async def collect(provider: LLMProvider) -> list[StreamChunk]:
    return [chunk async for chunk in provider.stream(request())]


class Sink:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:  # pragma: no cover - no se usa
        pass


# --------------------------------------------------------------- contrato base


class CompleteOnly(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="todo de una vez", stop_reason="stop")


def test_default_stream_wraps_complete() -> None:
    chunks = run(collect(CompleteOnly()))
    assert [c.text for c in chunks[:-1]] == ["todo de una vez"]
    assert chunks[-1].final and chunks[-1].response.content == "todo de una vez"


def test_fake_streams_word_by_word() -> None:
    chunks = run(collect(FakeLLM(script=[{"content": "uno dos tres"}])))
    assert [c.text for c in chunks[:-1]] == ["uno ", "dos ", "tres"]
    assert chunks[-1].response.content == "uno dos tres"


def test_complete_streaming_returns_final_and_reports_deltas() -> None:
    seen: list[str] = []
    response = run(
        complete_streaming(FakeLLM(script=[{"content": "a b c"}]), request(), seen.append)
    )
    assert response.content == "a b c"
    assert "".join(seen) == "a b c"


def test_complete_streaming_survives_a_failing_callback() -> None:
    def boom(_text: str) -> None:
        raise RuntimeError("ui rota")

    response = run(complete_streaming(FakeLLM(script=[{"content": "ok"}]), request(), boom))
    assert response.content == "ok"


def test_stream_without_final_chunk_is_an_error() -> None:
    class Broken(CompleteOnly):
        async def stream(self, request):
            yield StreamChunk(text="medio")

    with pytest.raises(ProviderError):
        run(complete_streaming(Broken(), request()))


def test_publisher_batches_by_interval_and_flushes_at_the_end() -> None:
    sink = Sink()
    now = [0.0]
    publisher = DeltaPublisher(
        sink, task_id="t1", node_id="n1", interval_s=1.0, clock=lambda: now[0]
    )
    publisher("a")
    publisher("b")
    assert sink.events == []  # todavía dentro del intervalo
    now[0] = 1.5
    publisher("c")
    assert [e.payload["text"] for e in sink.events] == ["abc"]
    publisher("d")
    publisher.flush()
    assert [e.payload["text"] for e in sink.events] == ["abc", "d"]
    assert all(e.event_type is EventType.TEXT_DELTA for e in sink.events)
    assert sink.events[0].task_id == "t1" and sink.events[0].node_id == "n1"


def test_complete_streaming_flushes_the_publisher() -> None:
    sink = Sink()
    publisher = DeltaPublisher(sink, task_id="t1", interval_s=60.0)
    run(complete_streaming(FakeLLM(script=[{"content": "x y"}]), request(), publisher))
    assert "".join(e.payload["text"] for e in sink.events) == "x y"


# --------------------------------------------------------------- envoltorios


def test_metered_stream_records_one_call_with_the_final_response() -> None:
    records: list = []
    recorder = NS(record=records.append)
    metered = MeteredProvider(FakeLLM(script=[{"content": "medido"}]), recorder)
    chunks = run(collect(metered))
    assert chunks[-1].response.content == "medido"
    assert len(records) == 1


def test_stream_restorer_handles_placeholders_split_across_deltas() -> None:
    vault = Vault()
    email = vault.token("EMAIL", "ana@example.com")
    ident = vault.token("ID", "secret_name")
    restorer = StreamRestorer(vault)
    text = f"Escribe a {email} sobre {ident}."
    pieces = [text[i : i + 3] for i in range(0, len(text), 3)]
    out = "".join(restorer.feed(piece) for piece in pieces) + restorer.flush()
    assert out == "Escribe a ana@example.com sobre secret_name."


# --------------------------------------------------------------- OpenAI


def _oai_chunk(content=None, tool_calls=None, finish=None, usage=None):
    choices = [] if content is None and tool_calls is None and finish is None else [
        NS(delta=NS(content=content, tool_calls=tool_calls), finish_reason=finish)
    ]
    return NS(choices=choices, usage=usage)


class _AsyncList:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self.items:
            yield item


class _HttpError(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status_code = status


class FakeOpenAI:
    def __init__(self, stream_items=None, fail_stream=False, stream_error=None):
        self.stream_items = stream_items or []
        self.fail_stream = fail_stream
        # Cómo falla el stream: por defecto, el servidor rechaza el streaming en sí.
        self.stream_error = stream_error or _HttpError(400, "Unsupported parameter: stream_options")
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            if self.fail_stream:
                raise self.stream_error
            return _AsyncList(self.stream_items)
        return NS(
            choices=[NS(message=NS(content="sin stream", tool_calls=None), finish_reason="stop")],
            usage=None,
        )


def _oai(client) -> OpenAICompatProvider:
    return OpenAICompatProvider(ProviderConfig(name="openai", model="gpt-4o"), client=client)


def test_openai_stream_rebuilds_text_tool_calls_and_usage() -> None:
    call0 = NS(index=0, id="c1", function=NS(name="read_file", arguments='{"pa'))
    call0b = NS(index=0, id=None, function=NS(name=None, arguments='th": "a.py"}'))
    client = FakeOpenAI(
        [
            _oai_chunk(content="Leo "),
            _oai_chunk(content="el archivo"),
            _oai_chunk(tool_calls=[call0]),
            _oai_chunk(tool_calls=[call0b], finish="tool_calls"),
            _oai_chunk(usage=NS(prompt_tokens=10, completion_tokens=4, total_tokens=14)),
        ]
    )
    chunks = run(collect(_oai(client)))
    assert [c.text for c in chunks[:-1]] == ["Leo ", "el archivo"]
    final = chunks[-1].response
    assert final.content == "Leo el archivo"
    assert final.tool_calls[0].id == "c1"
    assert final.tool_calls[0].name == "read_file"
    assert final.tool_calls[0].arguments == {"path": "a.py"}
    assert final.stop_reason == "tool_calls"
    assert final.usage.prompt_tokens == 10 and final.usage.completion_tokens == 4
    assert client.calls[0]["stream_options"] == {"include_usage": True}


def test_openai_stream_falls_back_to_complete_when_streaming_is_rejected() -> None:
    client = FakeOpenAI(fail_stream=True)
    chunks = run(collect(_oai(client)))
    assert chunks[-1].response.content == "sin stream"
    assert [bool(call.get("stream")) for call in client.calls] == [True, False]


def test_openai_stream_does_not_retry_at_once_on_a_transient_error() -> None:
    """Un 429 o un 5xx lo reintenta el envoltorio de reintentos (con espera y
    ``Retry-After``), no una segunda petición inmediata sin streaming."""
    import pytest

    from satellite.core.errors import ProviderError

    client = FakeOpenAI(fail_stream=True, stream_error=_HttpError(429, "rate limit exceeded"))
    with pytest.raises(ProviderError) as info:
        run(collect(_oai(client)))
    assert [bool(call.get("stream")) for call in client.calls] == [True]
    assert getattr(info.value.__cause__, "status_code", None) == 429


# --------------------------------------------------------------- Anthropic


class FakeAnthropic:
    def __init__(self, events):
        self.events = events
        self.calls: list[dict] = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _AsyncList(self.events)


def test_anthropic_stream_rebuilds_text_tool_use_and_usage() -> None:
    events = [
        NS(type="message_start", message=NS(id="msg_1", usage=NS(input_tokens=20, output_tokens=1))),
        NS(type="content_block_start", index=0, content_block=NS(type="text", text="")),
        NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text="Hola ")),
        NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text="mundo")),
        NS(type="content_block_start", index=1, content_block=NS(type="tool_use", id="tu1", name="lsp", input={})),
        NS(type="content_block_delta", index=1, delta=NS(type="input_json_delta", partial_json='{"action": ')),
        NS(type="content_block_delta", index=1, delta=NS(type="input_json_delta", partial_json='"hover"}')),
        NS(type="message_delta", delta=NS(stop_reason="tool_use"), usage=NS(output_tokens=9)),
        NS(type="message_stop"),
    ]
    client = FakeAnthropic(events)
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-sonnet-5-5"), client=client
    )
    chunks = run(collect(provider))
    assert [c.text for c in chunks[:-1]] == ["Hola ", "mundo"]
    final = chunks[-1].response
    assert final.content == "Hola mundo"
    assert final.tool_calls[0].name == "lsp"
    assert final.tool_calls[0].arguments == {"action": "hover"}
    assert final.stop_reason == "tool_calls"
    assert final.response_id == "msg_1"
    assert final.usage.completion_tokens == 9
    assert client.calls[0]["stream"] is True


# --------------------------------------------------------------- escritorio


def test_event_bridge_streams_deltas_live_without_storing_them() -> None:
    async def scenario():
        bridge = EventBridge()
        written: list = []

        class Store:
            async def append_event(self, task_id, event):
                written.append(event)

        bus = NS(subscribe=lambda handler: setattr(bridge, "_handler", handler))
        bridge.attach(bus, Store())
        queue = bridge.subscribe()
        delta = ExecutionEvent(
            event_type=EventType.TEXT_DELTA, task_id="t1", payload={"text": "hola"}
        )
        done = ExecutionEvent(event_type=EventType.TASK_FINISHED, task_id="t1")
        bridge._handler(delta)
        bridge._handler(done)
        await bridge.drain()
        live = [queue.get_nowait()["event"]["event_type"] for _ in range(queue.qsize())]
        replay = [item["event"]["event_type"] for item in bridge.since(0)]
        return live, replay, [e.event_type for e in written]

    live, replay, written = run(scenario())
    assert live == ["text_delta", "task_finished"]
    assert replay == ["task_finished"]
    assert written == [EventType.TASK_FINISHED]
