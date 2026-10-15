"""Puerto de aprobación para la TUI (modal + reanudar por proposal_id)."""

from __future__ import annotations

from satellite.approval.base import PendingApprovalPort


class TuiApproval(PendingApprovalPort):
    """``request`` queda esperando; la TUI abre un modal y llama a ``submit``."""

    def __init__(self, timeout_s: float | None = None) -> None:
        super().__init__(timeout_s=timeout_s)
