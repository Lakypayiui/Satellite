"""Manifiesto de tools **fuera del kernel** (F3).

Los ids de tools que no son kernel **no** viven en `satellite/kernel/manifest.py`:
el kernel es inmutable para agentes/skills y no debe crecer con cada tool nueva.
Este manifiesto es la fuente central agregada para validar los ids de runtime
(kernel + tools) y para excluir esos ids de la memoización de skills.

Vive en la raíz del paquete (como `commands.py`, `gitops.py`…) para no disparar el
``__init__`` de `satellite.tools` (que importa todas las tools y crearía un ciclo
con `satellite.skills`).
"""

from __future__ import annotations

from satellite.kernel.manifest import RESERVED_IDS

# Ids de tools fuera del kernel (pendientes de registrar en el runtime; el gate de
# permisos/aprobaciones es F4).
TOOL_IDS: frozenset[str] = frozenset(
    {
        "desktop_list_windows",
        "desktop_observe",
        "desktop_act",
    }
)


def reserved_ids() -> frozenset[str]:
    """Fuente central agregada: ids reservados del kernel + ids de tools."""
    return RESERVED_IDS | TOOL_IDS


def is_reserved_id(identifier: str) -> bool:
    """True si el id está reservado por el kernel **o** por el manifiesto de tools."""
    return identifier in reserved_ids()


def is_tool_id(identifier: str) -> bool:
    """True si el id pertenece al manifiesto de tools (no kernel)."""
    return identifier in TOOL_IDS
