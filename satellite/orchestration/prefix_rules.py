"""Reglas de ejecución del prefijo cacheable (F5).

1. **`PrefixGate`** — la **primera** llamada de un prefijo se hace en solitario:
   el resto espera a que termine y luego sale **en paralelo**. Efecto: el
   proveedor escribe la cache una sola vez y los seguidores la aciertan, en vez
   de provocar N escrituras simultáneas del mismo prefijo (que se pagan).
2. **`PrefixGroupTracker`** — dentro de una tarea, un mismo agente debería usar
   **siempre** el mismo prefijo (mismo modelo y mismas tools). Se vigila por
   ``(task_id, spec_id)`` y se avisa si cambia a mitad de tarea.

Ni el gate ni el tracker cambian la respuesta: sólo ordenan y observan.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

GROUP_STABLE = "stable"
GROUP_CHANGED = "prefix_changed_in_task"


class PrefixGate:
    """Serializa la primera llamada de cada prefijo y libera al resto después."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Event] = {}
        self._warmed: set[str] = set()

    def is_warmed(self, prefix_hash: str) -> bool:
        """True si ese prefijo ya tuvo su primera llamada."""
        return prefix_hash in self._warmed

    def awaiting(self) -> int:
        """Nº de prefijos con una primera llamada en curso (para diagnóstico)."""
        return len(self._pending)

    @asynccontextmanager
    async def enter(self, prefix_hash: str):
        """Cede ``True`` al dueño del calentamiento y ``False`` a los seguidores."""
        key = str(prefix_hash)
        if key in self._warmed or key in self._pending:
            event = self._pending.get(key)
            if event is not None:
                await event.wait()
            yield False
            return

        event = asyncio.Event()
        self._pending[key] = event
        try:
            yield True
        finally:
            # Se marca calentado aunque la llamada falle: así un error no deja a
            # los siguientes esperando en cadena a un prefijo que ya se intentó.
            self._warmed.add(key)
            self._pending.pop(key, None)
            event.set()

    def reset(self) -> None:
        """Olvida el estado (libera a quien estuviera esperando)."""
        for event in self._pending.values():
            event.set()
        self._pending.clear()
        self._warmed.clear()


@dataclass(frozen=True)
class PrefixGroup:
    """Llamadas de un agente dentro de una tarea."""

    task_id: str = ""
    spec_id: str = ""
    prefix_hash: str = ""
    model: str = ""
    calls: int = 0
    distinct_prefixes: int = 1

    @property
    def stable(self) -> bool:
        """True si todas las llamadas del grupo usaron el mismo prefijo."""
        return self.distinct_prefixes <= 1

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "spec_id": self.spec_id,
            "prefix_hash": self.prefix_hash,
            "model": self.model,
            "calls": self.calls,
            "distinct_prefixes": self.distinct_prefixes,
            "stable": self.stable,
        }


@dataclass
class _GroupState:
    first_hash: str
    model: str = ""
    calls: int = 0
    hashes: set = field(default_factory=set)


class PrefixGroupTracker:
    """Vigila que un agente no cambie de prefijo a mitad de tarea."""

    def __init__(self) -> None:
        self._groups: dict[tuple[str, str], _GroupState] = {}

    def observe(
        self,
        *,
        task_id: str,
        spec_id: str,
        prefix_hash: str,
        model: str = "",
    ) -> str:
        """Registra una llamada y devuelve ``stable`` o ``prefix_changed_in_task``."""
        key = (str(task_id), str(spec_id))
        state = self._groups.get(key)
        if state is None:
            state = _GroupState(first_hash=str(prefix_hash), model=str(model))
            self._groups[key] = state
        state.calls += 1
        state.hashes.add(str(prefix_hash))
        if state.hashes == {state.first_hash}:
            return GROUP_STABLE
        return GROUP_CHANGED

    def group(self, task_id: str, spec_id: str) -> PrefixGroup | None:
        """Estado del grupo ``(task_id, spec_id)``."""
        state = self._groups.get((str(task_id), str(spec_id)))
        if state is None:
            return None
        return PrefixGroup(
            task_id=str(task_id),
            spec_id=str(spec_id),
            prefix_hash=state.first_hash,
            model=state.model,
            calls=state.calls,
            distinct_prefixes=len(state.hashes),
        )

    def groups(self) -> list[PrefixGroup]:
        """Todos los grupos observados (orden estable por tarea y agente)."""
        return [
            group
            for key in sorted(self._groups)
            if (group := self.group(*key)) is not None
        ]

    def unstable_groups(self) -> list[PrefixGroup]:
        """Grupos cuyo prefijo cambió a mitad de tarea (cache desperdiciada)."""
        return [group for group in self.groups() if not group.stable]

    def reset(self) -> None:
        """Olvida el estado observado."""
        self._groups.clear()
