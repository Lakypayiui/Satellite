"""Backend genérico basado en adaptador (Windows/macOS/Linux).

Concentra **toda la seguridad común** para que ningún backend de plataforma pueda
esquivarla:
- snapshots compactos y redacción de campos sensibles;
- ``snapshot_id`` vigente (cada observación invalida las anteriores);
- validación determinista de la acción (allowlist, target operable, texto,
  sensible, coordenadas apagadas);
- resolución ``ref`` → clave de control nativo;
- re-observación real y ``changed``;
- ``inspect`` para la capa de política de F4.

Los adaptadores sólo traducen listar/observar/actuar sobre controles nativos.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .actions import SNAPSHOT_OBSOLETE, TARGET_MISSING, validate_action
from .base import (
    ActResult,
    Action,
    Capabilities,
    DesktopBackend,
    Element,
    ObserveLimits,
    Snapshot,
    WindowInfo,
)
from .snapshot import build_snapshot


@runtime_checkable
class DesktopAdapter(Protocol):
    """Capa nativa mínima (inyectable en tests; import perezoso en producción)."""

    def list_windows(self, *, max_windows: int) -> list[dict[str, Any]]: ...

    def window_elements(self, window_id: str, *, max_elements: int) -> list[dict[str, Any]]: ...

    def act(self, window_id: str, node_key: str, action: dict[str, Any]) -> dict[str, Any]: ...


class AdapterDesktopBackend(DesktopBackend):
    """Lógica común de observación/acción sobre un adaptador de plataforma."""

    platform: str = "desktop"
    sources: tuple[str, ...] = ()
    can_act: bool = False
    supported_actions: tuple[str, ...] = ()
    max_windows: int = 40

    def __init__(self, adapter: DesktopAdapter | None = None) -> None:
        self._adapter = adapter
        self._refs: dict[str, dict[str, str]] = {}
        self._key_by_ref: dict[str, dict[str, str]] = {}
        self._last: dict[str, Snapshot] = {}
        self._by_id: dict[str, str] = {}
        self._seq: dict[str, int] = {}

    def _make_adapter(self) -> DesktopAdapter:
        """Construye el adaptador nativo (import perezoso en el backend)."""
        raise NotImplementedError

    def _use(self) -> DesktopAdapter:
        if self._adapter is None:
            self._adapter = self._make_adapter()
        return self._adapter

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            can_observe=True,
            can_act=self.can_act,
            actions=tuple(self.supported_actions),
            coordinate_fallback=False,
            sources=tuple(self.sources),
        )

    async def list_windows(self) -> list[WindowInfo]:
        return [
            self._window_info(node)
            for node in self._use().list_windows(max_windows=self.max_windows)
        ]

    async def observe(
        self,
        window: WindowInfo | str,
        limits: ObserveLimits | None = None,
    ) -> Snapshot:
        topes = limits or ObserveLimits()
        info = await self._resolve(window)
        # Se pide un elemento de más para poder marcar el truncado.
        nodes = self._use().window_elements(info.id, max_elements=topes.max_elements + 1)
        self._seq[info.id] = self._seq.get(info.id, 0) + 1
        snapshot = build_snapshot(
            snapshot_id=f"{self.platform}:{info.id}:{self._seq[info.id]}",
            window=info,
            elements=self._elements(info.id, nodes),
            limits=topes,
            previous=self._last.get(info.id),
        )
        # Obsolescencia: un snapshot nuevo invalida el anterior de esa ventana.
        anterior = self._last.get(info.id)
        if anterior is not None:
            self._by_id.pop(anterior.snapshot_id, None)
        self._last[info.id] = snapshot
        self._by_id[snapshot.snapshot_id] = info.id
        return snapshot

    async def inspect(self, snapshot_id: str) -> Snapshot | None:
        """Snapshot vigente para la capa de política (``None`` si obsoleto)."""
        window_id = self._by_id.get(snapshot_id)
        if window_id is None:
            return None
        snapshot = self._last.get(window_id)
        if snapshot is None or snapshot.snapshot_id != snapshot_id:
            return None
        return snapshot

    async def act(self, snapshot_id: str, action: Action) -> ActResult:
        window_id = self._by_id.get(snapshot_id)
        if window_id is None:
            return ActResult(
                ok=False,
                action=action.kind,
                ref=action.target,
                code=SNAPSHOT_OBSOLETE,
                error="snapshot obsoleto o desconocido",
                before_snapshot_id=snapshot_id,
            )
        snapshot = self._last.get(window_id)
        # Misma validación que F1/F3: allowlist, obsolescencia, target operable,
        # texto acotado, sensible y coordenadas (siempre apagadas).
        decision = validate_action(
            action,
            snapshot=snapshot,
            snapshot_id=snapshot_id,
            allow_coordinates=False,
        )
        if not decision.ok:
            return ActResult(
                ok=False,
                action=action.kind,
                ref=action.target,
                code=decision.code,
                error=decision.reason,
                before_snapshot_id=snapshot_id,
            )
        # Scroll sin target = área principal de la ventana (compatible con los tres
        # backends: cada adaptador decide cómo desplazarla).
        if action.kind == "scroll" and not action.target:
            node_key = ""
        else:
            node_key = self._key_by_ref.get(window_id, {}).get(action.target)
            if not node_key:
                return ActResult(
                    ok=False,
                    action=action.kind,
                    ref=action.target,
                    code=TARGET_MISSING,
                    error="la referencia no resuelve a un control del snapshot",
                    before_snapshot_id=snapshot_id,
                )
        outcome = self._use().act(window_id, node_key, action.to_dict())
        if not outcome.get("ok", False):
            return ActResult(
                ok=False,
                action=action.kind,
                ref=action.target,
                code=str(outcome.get("code") or "act_failed"),
                error=str(outcome.get("error") or ""),
                before_snapshot_id=snapshot_id,
            )
        # Re-observar y reportar si cambió algo de verdad.
        assert snapshot is not None  # garantizado por validate_action
        after = await self.observe(snapshot.window)
        return ActResult(
            ok=True,
            action=action.kind,
            ref=action.target,
            changed=after.signature() != snapshot.signature(),
            before_snapshot_id=snapshot_id,
            after_snapshot_id=after.snapshot_id,
        )

    @staticmethod
    def _window_info(node: dict[str, Any]) -> WindowInfo:
        return WindowInfo(
            id=str(node.get("id", "")),
            title=str(node.get("title", "")),
            app=str(node.get("app", "")),
            active=bool(node.get("active", False)),
        )

    async def _resolve(self, window: WindowInfo | str) -> WindowInfo:
        if isinstance(window, WindowInfo):
            return window
        wid = str(window)
        for info in await self.list_windows():
            if info.id == wid:
                return info
        return WindowInfo(id=wid)

    def _elements(self, window_id: str, nodes: list[dict[str, Any]]) -> tuple[Element, ...]:
        cache = self._refs.setdefault(window_id, {})
        claves = self._key_by_ref.setdefault(window_id, {})
        counter = len(cache)
        elements: list[Element] = []
        for position, node in enumerate(nodes):
            key = str(node.get("key") or f"#{position}")
            ref = cache.get(key)
            if ref is None:
                counter += 1
                ref = f"e{counter}"
                cache[key] = ref
            # ref -> clave estable del control (para resolver el target en act()).
            claves[ref] = key
            sensitive = bool(node.get("sensitive", False))
            elements.append(
                Element(
                    ref=ref,
                    role=str(node.get("role") or "Unknown"),
                    name=str(node.get("name", "")),
                    # Defensa en profundidad: un campo sensible nunca se expone.
                    value="" if sensitive else str(node.get("value", "")),
                    enabled=bool(node.get("enabled", True)),
                    visible=bool(node.get("visible", True)),
                    interactable=bool(node.get("interactable", False)),
                    sensitive=sensitive,
                    actions=tuple(str(a) for a in node.get("actions", ())),
                    automation_id=str(node.get("automation_id", "")),
                )
            )
        return tuple(elements)
