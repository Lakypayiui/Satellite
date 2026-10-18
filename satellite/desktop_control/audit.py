"""Auditoría de acciones de control de escritorio (F4).

Reutiliza ``satellite.kernel.audit.AuditLog`` (append-only, encadenado por hash)
sin añadir política ni ejecución al kernel. **Nunca** registra contenido de UI:
ni nombres, ni valores, ni el texto escrito. Sólo app, hash de la referencia,
tipo de acción, snapshot id y resultado.
"""

from __future__ import annotations

import hashlib

from satellite.kernel.audit import AuditLog
from satellite.kernel.paths import DataPaths, resolve_paths


def ref_hash(ref: object) -> str:
    """Hash corto de la referencia efímera (no se guarda la ref cruda)."""
    text = str(ref or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def audit_log_for(paths: DataPaths | None = None) -> AuditLog:
    """``AuditLog`` en la raíz de auditoría global (``<global>/audit``)."""
    return AuditLog((paths or resolve_paths(None)).audit_root)


def audit_desktop_action(
    log: AuditLog | None,
    *,
    action: str,
    app: object = "",
    ref: object = "",
    action_type: str = "",
    snapshot_id: str = "",
    ok: bool = True,
    code: str = "",
    session_id: str = "",
) -> dict | None:
    """Registra una acción (o un intento) sin contenido de UI.

    ``details`` sólo lleva metadatos deterministas; jamás ``name``/``value``/
    ``text``. Devuelve la entrada escrita (``None`` si no hay log).
    """
    if log is None:
        return None
    details = {
        "app": str(app or ""),
        "ref_hash": ref_hash(ref),
        "action_type": str(action_type or ""),
        "snapshot_id": str(snapshot_id or ""),
        "ok": bool(ok),
        "code": str(code or ""),
        "session_id": str(session_id or ""),
    }
    return log.record(str(action), actor="desktop_control", target=str(app or ""), details=details)
