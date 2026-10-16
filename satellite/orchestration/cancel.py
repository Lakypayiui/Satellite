"""Cancelación cooperativa: un token que viaja por el runtime como la aprobación.

El runtime no se mata a sí mismo sin dejar el estado a medias, así que la cancelación se
**pide** y se observa en puntos concretos: al empezar cada nodo, y en las siguientes tandas
dentro del bucle del agente y antes de cada herramienta. El corte duro de la tarea sigue
existiendo, pero como último recurso y diciendo que fue ese el camino.
"""

from __future__ import annotations

import asyncio
import contextlib


class CancelledTask(Exception):
    """Cancelación pedida por el usuario (no es un fallo del sistema)."""


class CancelToken:
    """Señal de cancelación de un runtime (una ejecución cada vez)."""

    def __init__(self) -> None:
        self._cancelled = False
        self._reason = ""
        self._event: asyncio.Event | None = None

    @property
    def cancelled(self) -> bool:
        """True si ya se pidió la cancelación."""
        return self._cancelled

    @property
    def reason(self) -> str:
        """Por qué se pidió (la primera razón es la que vale)."""
        return self._reason

    def request(self, reason: str = "") -> None:
        """Pide la cancelación. Idempotente: repetirla no cambia la razón ni el estado."""
        if self._cancelled:
            return
        self._cancelled = True
        self._reason = reason or "cancelado"
        # Quien esté esperando la cancelación (una llamada al proveedor en vuelo) se entera
        # aquí: sin esto, la carrera se quedaría esperando para siempre.
        if self._event is not None:
            self._event.set()

    def reset(self) -> None:
        """Deja el token listo para la siguiente ejecución."""
        self._cancelled = False
        self._reason = ""
        if self._event is not None:
            self._event = asyncio.Event()

    def event(self) -> asyncio.Event:
        """Evento para **esperar** la cancelación (carreras con llamadas largas)."""
        if self._event is None:
            self._event = asyncio.Event()
            if self._cancelled:
                self._event.set()
        return self._event

    def raise_if_cancelled(self) -> None:
        """Levanta ``CancelledTask`` si ya se pidió la cancelación."""
        if self._cancelled:
            raise CancelledTask(self._reason)


async def race_cancel(token, awaitable):
    """Espera ``awaitable``, pero lo abandona si piden cancelar antes.

    Devuelve ``(valor, cancelado)``. Si gana la cancelación, la llamada en vuelo se
    **aborta** en vez de esperarse a que acabe, que es lo que hace útil poder cancelar:
    si no, cancelar una llamada al proveedor de 40 s no ahorraría nada.
    """
    if token is None:
        return await awaitable, False
    llamada = asyncio.ensure_future(awaitable)
    aviso = asyncio.ensure_future(token.event().wait())
    try:
        listas, _ = await asyncio.wait({llamada, aviso}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        llamada.cancel()
        aviso.cancel()
        raise
    if llamada in listas:
        aviso.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await aviso
        return llamada.result(), False  # un fallo real del proveedor sigue siendo un fallo
    aviso.cancel()
    llamada.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await llamada
    return None, True
