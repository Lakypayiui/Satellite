"""Detección de deriva del prefijo (``CACHE_PREFIX_DRIFT``).

Regla del plan: **sólo hay deriva** si cambia el ``prefix_hash`` **con los cuatro
fingerprints iguales** (spec, catálogo, tools y modelo). Cualquier cambio
legítimo de catálogo/modelo/tools se clasifica con su propio ``kind`` y **no**
dispara alerta de deriva.
"""

from __future__ import annotations

from dataclasses import dataclass

from satellite.observability.call_log import (
    KIND_CATALOG_CHANGED,
    KIND_CHANGED,
    KIND_DRIFT,
    KIND_MODEL_OR_TOOLS_CHANGED,
)


@dataclass(frozen=True)
class PrefixSnapshot:
    """Estado del prefijo de una llamada anterior."""

    prefix_hash: str = ""
    spec_fp: str = ""
    catalog_fp: str = ""
    tools_fp: str = ""
    model: str = ""


class PrefixDriftDetector:
    """Compara llamadas del mismo ``(task_id, agente/rol)`` y clasifica el cambio."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], PrefixSnapshot] = {}

    def classify(
        self,
        *,
        task_id: str,
        agent_id: str,
        model: str,
        prefix_hash: str,
        spec_fp: str = "",
        catalog_fp: str = "",
        tools_fp: str = "",
    ) -> str:
        """Devuelve ``""`` (sin cambios) o el ``kind`` del cambio."""
        key = (str(task_id), str(agent_id))
        snapshot = PrefixSnapshot(
            prefix_hash=str(prefix_hash),
            spec_fp=str(spec_fp),
            catalog_fp=str(catalog_fp),
            tools_fp=str(tools_fp),
            model=str(model),
        )
        previous = self._last.get(key)
        self._last[key] = snapshot
        if previous is None:
            return ""
        if previous.prefix_hash == snapshot.prefix_hash and previous.model == snapshot.model:
            return ""

        changed: list[str] = []
        if snapshot.spec_fp != previous.spec_fp:
            changed.append("spec")
        if snapshot.catalog_fp != previous.catalog_fp:
            changed.append("catalog")
        if snapshot.tools_fp != previous.tools_fp:
            changed.append("tools")
        if snapshot.model != previous.model:
            changed.append("model")

        if not changed:
            # mismos datos de entrada pero el prefijo cambió -> deriva real
            return KIND_DRIFT
        if changed == ["catalog"]:
            return KIND_CATALOG_CHANGED
        if set(changed) <= {"model", "tools"}:
            return KIND_MODEL_OR_TOOLS_CHANGED
        return KIND_CHANGED

    def snapshot_for(self, task_id: str, agent_id: str) -> PrefixSnapshot | None:
        """Último estado conocido (útil en tests/diagnóstico)."""
        return self._last.get((str(task_id), str(agent_id)))

    def reset(self) -> None:
        """Olvida el estado (cambio de tarea o de proveedor)."""
        self._last.clear()
