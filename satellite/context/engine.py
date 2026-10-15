"""DefaultContextEngine: resolución just-in-time con snapshots por nodo."""

from __future__ import annotations

from pathlib import Path

from satellite.core.context_models import (
    ContextBundle,
    ContextLimits,
    ContextRef,
    ContextSnapshot,
)
from satellite.context.base import ContextEngine
from satellite.context.project_index import ProjectIndex
from satellite.context.resolver import ContextResolver


class DefaultContextEngine(ContextEngine):
    """Resuelve contexto solo cuando se pide, con auditoría por nodo."""

    def __init__(
        self,
        project_root: Path,
        limits: ContextLimits | None = None,
        resolver: ContextResolver | None = None,
    ) -> None:
        self.project_root = project_root
        self.default_limits = limits
        self.resolver = resolver or ContextResolver(project_root, ProjectIndex(project_root))
        self._snapshots: dict[str, ContextSnapshot] = {}
        self._last_bundle: ContextBundle | None = None

    async def resolve(
        self,
        refs: list[ContextRef] | tuple[ContextRef, ...],
        limits: ContextLimits | None = None,
    ) -> ContextBundle:
        """Delega en el resolver dentro de los límites."""
        effective = limits or self.default_limits
        bundle = await self.resolver.resolve_many(refs, effective)
        self._last_bundle = bundle
        return bundle

    async def expand(
        self,
        node_id: str,
        new_refs: list[ContextRef] | tuple[ContextRef, ...],
    ) -> ContextBundle:
        """Resuelve SOLO los refs nuevos; el runner decide cómo fusionar."""
        return await self.resolve(new_refs)

    async def invalidate(self, paths: list[str]) -> None:
        """Marca los paths como desactualizados en el índice."""
        for p in paths:
            rel = self.resolver.index._relpath(self.project_root / p)
            self.resolver.index._stats.pop(rel, None)
            self.resolver.index._structure.pop(rel, None)

    async def snapshot(self, node_id: str) -> ContextSnapshot:
        """Snapshot del último bundle resuelto para node_id."""
        if node_id in self._snapshots:
            return self._snapshots[node_id]
        if self._last_bundle is not None:
            return ContextSnapshot(
                node_id=node_id,
                refs=tuple(s.ref for s in self._last_bundle.sections),
                total_chars=self._last_bundle.total_chars,
                total_tokens=self._last_bundle.total_tokens,
            )
        return ContextSnapshot(node_id=node_id)

    def _register_snapshot(self, node_id: str, bundle: ContextBundle) -> None:
        from datetime import datetime, timezone

        self._snapshots[node_id] = ContextSnapshot(
            node_id=node_id,
            refs=tuple(s.ref for s in bundle.sections),
            total_chars=bundle.total_chars,
            total_tokens=bundle.total_tokens,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
