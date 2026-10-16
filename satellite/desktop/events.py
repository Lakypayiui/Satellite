"""Puente entre el bus del runtime y los clientes de la app.

El bus es síncrono y en memoria. Aquí se convierte en un flujo numerado (para poder
reanudar sin repetir ni saltarse nada) y, de paso, se **persiste cada evento**: hasta
ahora nadie llamaba a ``FileStore.append_event``, así que ``.satellite/executions``
quedaba vacío y ``/status`` decía 0 eventos. Esto lo arregla para todos (TUI y CLI
incluidos), no sólo para el escritorio.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator

HISTORY_SIZE = 500
HEARTBEAT_S = 15.0


class EventBridge:
    """Reparte los eventos del runtime, los guarda y los sirve por SSE."""

    def __init__(self, *, history: int = HISTORY_SIZE) -> None:
        self._history: deque[dict] = deque(maxlen=history)
        self._queues: set[asyncio.Queue] = set()
        self._index = 0
        self._store = None
        self._attached = False
        self._tasks: set[asyncio.Task] = set()

    # --- enganche con el runtime ---

    @property
    def attached(self) -> bool:
        """True si ya está escuchando el bus del runtime del proyecto."""
        return self._attached

    def attach(self, bus, store=None) -> None:
        """Se suscribe al bus. El almacén es opcional: sin él no se persiste."""
        self._store = store
        self._attached = True
        bus.subscribe(self._on_event)

    def _on_event(self, event) -> None:
        """Llega desde el runtime, en el mismo bucle: reparte y persiste."""
        self._index += 1
        item = {"index": self._index, "event": event.to_dict()}
        self._history.append(item)
        for queue in list(self._queues):
            queue.put_nowait(item)
        self._persist(event)

    def _persist(self, event) -> None:
        """Guarda el evento sin poder tumbar la ejecución que lo produjo."""
        if self._store is None or not getattr(event, "task_id", ""):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # sin bucle en marcha no hay dónde programarlo: se pierde el registro
        task = loop.create_task(self._write(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _write(self, event) -> None:
        try:
            await self._store.append_event(event.task_id, event)
        except Exception:  # noqa: BLE001 — el registro nunca puede tumbar una tarea
            return

    async def drain(self) -> None:
        """Espera a que terminen las escrituras pendientes (cierre ordenado)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # --- para los clientes ---

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    def since(self, after: int = 0) -> list[dict]:
        """Eventos ya vistos con índice mayor que ``after``."""
        return [item for item in self._history if item["index"] > after]

    @property
    def last_index(self) -> int:
        """Último índice emitido (para que el cliente sepa por dónde va)."""
        return self._index


def _frame(item: dict) -> str:
    return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


async def event_stream(
    bridge: EventBridge,
    after: int = 0,
    *,
    heartbeat_s: float = HEARTBEAT_S,
    stop: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """Generador SSE: primero lo que ya había, después lo que vaya llegando.

    El latido es un comentario SSE, así que un cliente puede ignorarlo y una conexión
    medio muerta se nota en vez de quedarse colgada en silencio.
    """
    queue = bridge.subscribe()
    try:
        for item in bridge.since(after):
            yield _frame(item)
        while stop is None or not stop.is_set():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except asyncio.TimeoutError:
                yield ": latido\n\n"
                continue
            yield _frame(item)
    finally:
        bridge.unsubscribe(queue)
