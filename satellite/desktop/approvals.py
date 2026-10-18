"""Puerto de aprobación para la app de escritorio.

La interfaz no decide nada por su cuenta: pide la lista de propuestas pendientes, muestra
el motivo y las opciones, y responde por ``POST /api/approvals/{proposal_id}``. Mientras
tanto el agente está esperando en ``request``, que es exactamente el contrato que ya usan
la TUI y la CLI.
"""

from __future__ import annotations

from satellite.approval.base import PendingApprovalPort
from satellite.core.proposal import ApprovalDecision, ApprovalStatus

DECISIONS = ("approve", "reject", "needs_changes")
FEEDBACK = ("once", "session")
# Opciones que se ofrecen según el tipo de propuesta. "session" (recordar el permiso
# durante esta sesión) sólo tiene sentido para accesos: rutas y comandos.
OPTIONS_BY_KIND = {
    "command": ("once", "session", "reject"),
    "path": ("once", "session", "reject"),
    # La lectura de contexto externo es de un solo turno: no se ofrece «session».
    "context_path": ("once", "reject"),
    # El grant de control de escritorio es siempre por sesión+app (no hay "una vez"
    # para observar/controlar una app); se revoca desde la política.
    "desktop_control": ("approve", "reject"),
}
DEFAULT_OPTIONS = ("approve", "reject", "needs_changes")


class DesktopApproval(PendingApprovalPort):
    """``request`` espera; la app responde con ``submit`` cuando el usuario decide.

    Sin timeout por defecto a propósito: con alguien mirando la pantalla, rechazar solo
    por tardar sería mentir sobre lo que ha decidido el usuario. Si un turno queda
    colgado, cerrar la ventana lo resuelve sin corromper la sesión.
    """

    def __init__(self, timeout_s: float | None = None) -> None:
        super().__init__(timeout_s=timeout_s)

    def describe(self) -> list[dict]:
        """Propuestas pendientes, con el motivo y las opciones que la app debe ofrecer."""
        out: list[dict] = []
        for item in self.pending():
            kind = str(item.get("kind", ""))
            details = item.get("details") or {}
            out.append(
                {
                    "proposal_id": item.get("proposal_id", ""),
                    "kind": kind,
                    "task_id": item.get("task_id", ""),
                    "summary": item.get("summary", ""),
                    "reason": details.get("consequence", ""),
                    "details": details,
                    "options": list(OPTIONS_BY_KIND.get(kind, DEFAULT_OPTIONS)),
                }
            )
        return out


def decision_from(decision: str, *, feedback: str = "", reason: str = "") -> ApprovalDecision:
    """Traduce lo que manda la interfaz a la decisión del contrato.

    Aprobar sin decir ``feedback`` concede **una vez**: nunca se convierte en un permiso
    permanente por omisión.
    """
    kind = (decision or "").strip().lower()
    if kind not in DECISIONS:
        raise ValueError(f"decisión desconocida: {decision!r} (usa {'|'.join(DECISIONS)})")
    given = (feedback or "").strip().lower()
    if given and given not in FEEDBACK:
        raise ValueError(f"feedback desconocido: {feedback!r} (usa {'|'.join(FEEDBACK)})")
    if kind == "approve":
        return ApprovalDecision(
            status=ApprovalStatus.APPROVED,
            reason=reason or "aprobado desde la app",
            feedback=given or "once",
        )
    if kind == "needs_changes":
        return ApprovalDecision(
            status=ApprovalStatus.NEEDS_CHANGES,
            reason=reason or "pide cambios desde la app",
            feedback=given,
        )
    return ApprovalDecision(
        status=ApprovalStatus.REJECTED, reason=reason or "rechazado desde la app"
    )
