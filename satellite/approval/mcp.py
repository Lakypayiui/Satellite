"""Aprobación desde MCP (tool ``satellite_approve``)."""

from __future__ import annotations

from satellite.approval.base import PendingApprovalPort


class McpApproval(PendingApprovalPort):
    """``request`` queda esperando; la tool ``satellite_approve`` llama a ``submit``."""

    def __init__(self, timeout_s: float | None = None) -> None:
        super().__init__(timeout_s=timeout_s)
