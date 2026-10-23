"""Consumo de ``LLMProvider.stream`` con publicación de deltas en el bus.

``complete_streaming`` es el punto único por el que el runtime pide una respuesta en
streaming: consume el stream, publica el texto como eventos ``TEXT_DELTA`` agrupados y
devuelve la respuesta final, igual que ``complete``. Así el resto del código no cambia de
contrato y la UI ve el texto mientras el modelo lo escribe.
"""

from __future__ import annotations

import time
from typing import Callable

from satellite.core.errors import ProviderError
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.messages import LLMRequest, LLMResponse
from satellite.providers.base import LLMProvider

# Los deltas se agrupan para no inundar SSE ni la TUI con un evento por token.
DELTA_INTERVAL_S = 0.05


class DeltaPublisher:
    """Agrupa deltas de texto y los emite como ``TEXT_DELTA`` en un sink.

    No usa temporizadores: un delta que llega pasado el intervalo vacía el búfer, y
    ``flush`` vacía lo pendiente al terminar la llamada.
    """

    def __init__(
        self,
        sink,
        *,
        task_id: str,
        node_id: str | None = None,
        agent_id: str = "",
        segment: int = 0,
        interval_s: float = DELTA_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sink = sink
        self.task_id = task_id
        self.node_id = node_id
        self.agent_id = agent_id
        self.segment = segment
        self.interval_s = interval_s
        self.clock = clock
        self._buffer: list[str] = []
        self._last = clock()
        self.emitted = 0

    def __call__(self, text: str) -> None:
        if not text:
            return
        self._buffer.append(text)
        if self.clock() - self._last >= self.interval_s:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        self._last = self.clock()
        if self.sink is None or not self.task_id:
            return
        payload = {"text": text}
        if self.agent_id:
            payload["agent_id"] = self.agent_id
        if self.segment:
            payload["segment"] = self.segment
        self.sink.emit(
            ExecutionEvent(
                event_type=EventType.TEXT_DELTA,
                task_id=self.task_id,
                node_id=self.node_id,
                payload=payload,
            )
        )
        self.emitted += 1


async def complete_streaming(
    provider: LLMProvider,
    request: LLMRequest,
    on_text: Callable[[str], None] | None = None,
) -> LLMResponse:
    """Pide la respuesta en streaming y devuelve la respuesta final normalizada.

    ``on_text`` recibe cada delta de texto. Un error del callback nunca corta la
    llamada: mostrar progreso no puede romper el turno.
    """
    stream = getattr(provider, "stream", None)
    if not callable(stream):
        # Un proveedor que no hereda de LLMProvider (adaptadores de terceros, dobles de
        # test) sólo garantiza ``complete``: se usa tal cual, sin progreso intermedio.
        response = await provider.complete(request)
        if response.content and on_text is not None:
            try:
                on_text(response.content)
            except Exception:  # noqa: BLE001 — el progreso es best-effort
                pass
        if isinstance(on_text, DeltaPublisher):
            on_text.flush()
        return response
    final: LLMResponse | None = None
    async for chunk in stream(request):
        if chunk.response is not None:
            final = chunk.response
            continue
        if chunk.text and on_text is not None:
            try:
                on_text(chunk.text)
            except Exception:  # noqa: BLE001 — el progreso es best-effort
                pass
    if final is None:
        raise ProviderError("el stream del proveedor terminó sin respuesta final")
    if isinstance(on_text, DeltaPublisher):
        on_text.flush()
    return final
