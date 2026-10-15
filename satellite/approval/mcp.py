"""Aprobación desde MCP: **no aprueba**, deja constancia y devuelve el control.

MCP es el único transporte sin humano en la conversación (y el servidor no
implementa ``elicitation``), así que una tool que aprobara sería una vía para
aprobarse solo: un host con inyección de prompt podría crear agentes o skills
sin que nadie lo revise. Las decisiones se toman en el CLI o en la TUI; desde MCP
solo se pueden **consultar** (tool de lectura ``satellite_approvals``).
"""

from __future__ import annotations

from satellite.approval.base import ApprovalContext, ApprovalPort, proposal_id_of
from satellite.core.proposal import ApprovalDecision, ApprovalStatus

DENIED_REASON = (
    "requiere decisión humana: apruébala en el CLI o en la TUI (MCP no aprueba)"
)


class McpApproval(ApprovalPort):
    """Nunca aprueba y nunca bloquea: registra la petición y la rechaza.

    Sustituye al puerto que esperaba a ``satellite_approve``: aquél dejaba la
    ejecución colgada indefinidamente y permitía que el propio host resolviera la
    aprobación. Aquí la propuesta queda **visible** y sin decidir.
    """

    def __init__(self) -> None:
        self._awaiting: list[dict] = []

    async def request(self, proposal: object, ctx: ApprovalContext) -> ApprovalDecision:
        """Anota la propuesta y rechaza: la decisión es de un humano."""
        proposal_id = proposal_id_of(proposal, ctx)
        if not any(item["proposal_id"] == proposal_id for item in self._awaiting):
            self._awaiting.append(
                {
                    "proposal_id": proposal_id,
                    "kind": ctx.kind,
                    "task_id": ctx.task_id,
                    "summary": ctx.summary,
                    "mode": ctx.mode,
                }
            )
        return ApprovalDecision(status=ApprovalStatus.REJECTED, reason=DENIED_REASON)

    def pending(self) -> list[dict]:
        """Propuestas esperando una decisión humana (solo informativo)."""
        return [dict(item) for item in self._awaiting]
