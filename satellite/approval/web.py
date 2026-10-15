"""Aprobación desde la consola web (modal + reanudar por task_id)."""

from __future__ import annotations

from satellite.approval.base import PendingApprovalPort


class WebApproval(PendingApprovalPort):
    """``request`` queda esperando; ``POST /api/run/{id}/approve`` llama a ``submit``."""

    def __init__(self, timeout_s: float | None = None) -> None:
        super().__init__(timeout_s=timeout_s)
