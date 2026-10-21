"""Kernel del framework: rutas de datos, políticas, manifiesto, guard y auditoría.

El kernel es inmutable para agentes/skills/modos: sólo lo cambia una
actualización del paquete. Aquí vive el código determinista que gobierna los
límites (qué se puede escribir, qué se aprueba, qué queda auditado).
"""

from satellite.kernel.audit import AuditLog
from satellite.kernel.guard import (
    KernelWriteError,
    assert_write_allowed,
    is_write_allowed,
    package_root,
    writable_roots,
)
from satellite.kernel.manifest import (
    RESERVED_AGENT_IDS,
    RESERVED_IDS,
    RESERVED_SKILL_IDS,
    SPAWNABLE_RESERVED_AGENT_IDS,
    is_reserved,
    sha256_bytes,
    sha256_file,
    verify_and_restore,
)
from satellite.kernel.paths import DataPaths, ensure_dirs, resolve_paths
from satellite.kernel.policy import PolicyMode, Quotas, Settings, load_settings

__all__ = [
    "AuditLog",
    "KernelWriteError",
    "assert_write_allowed",
    "is_write_allowed",
    "package_root",
    "writable_roots",
    "RESERVED_AGENT_IDS",
    "RESERVED_SKILL_IDS",
    "RESERVED_IDS",
    "SPAWNABLE_RESERVED_AGENT_IDS",
    "is_reserved",
    "sha256_bytes",
    "sha256_file",
    "verify_and_restore",
    "DataPaths",
    "ensure_dirs",
    "resolve_paths",
    "PolicyMode",
    "Quotas",
    "Settings",
    "load_settings",
]
