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

# Diagnóstico de caché de Anthropic (best-effort): la API compara dos peticiones
# consecutivas y dice dónde divergió el prefijo.
DIAGNOSIS_INCONCLUSIVE = "inconcluso"

_DIAGNOSIS_KINDS: dict[str, str] = {
    "model_changed": KIND_MODEL_OR_TOOLS_CHANGED,
    "tools_changed": KIND_MODEL_OR_TOOLS_CHANGED,
    # `system_changed` y `messages_changed` son cambios genéricos: nuestras
    # huellas dicen cuál de ellos y si es deriva. Nota: el catálogo viaja en un
    # bloque USER, así que un cambio **suyo** aparece como `messages_changed`.
    "system_changed": KIND_CHANGED,
    "messages_changed": KIND_CHANGED,
}


def diagnosis_kind(diagnostics: dict | None) -> str:
    """Traduce el diagnóstico de la API a nuestro veredicto.

    Devuelve ``""`` (comparó y no hay divergencia), un ``kind`` de cambio, o
    ``DIAGNOSIS_INCONCLUSIVE`` cuando la API no da un veredicto: primer turno del
    prefijo, comparación aún en curso, ``previous_message_not_found`` (huella
    caducada o de otro espacio de trabajo) o ``unavailable`` (parámetros que
    afectan al prompt, o conversación demasiado larga).
    """
    if not isinstance(diagnostics, dict):
        return DIAGNOSIS_INCONCLUSIVE
    if not diagnostics.get("previous_message_id"):
        return DIAGNOSIS_INCONCLUSIVE  # primer turno: no hay nada que comparar
    if not diagnostics.get("present"):
        # El campo no vino: comparó y no encontró divergencia.
        return ""
    reason = diagnostics.get("cache_miss_reason")
    if reason is None:
        return DIAGNOSIS_INCONCLUSIVE  # la comparación seguía en curso
    kind = _DIAGNOSIS_KINDS.get(str(reason.get("type", "")))
    return kind or DIAGNOSIS_INCONCLUSIVE


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
