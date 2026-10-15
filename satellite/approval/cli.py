"""Aprobación por consola (CLI)."""

from __future__ import annotations

from typing import Callable

from satellite.approval.base import ApprovalContext, ApprovalPort
from satellite.core.proposal import ApprovalDecision, ApprovalStatus


class CliApproval(ApprovalPort):
    """Pregunta por consola; ``ask``/``echo`` son inyectables para tests."""

    def __init__(
        self,
        ask: Callable[[str], str] | None = None,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        self._ask = ask or input
        self._echo = echo or print

    async def request(self, proposal: object, ctx: ApprovalContext) -> ApprovalDecision:
        """Muestra un resumen y pide s/n/cambios hasta obtener una respuesta válida."""
        self._echo(f"[satellite] aprobación requerida ({ctx.kind}): {ctx.summary}")
        if ctx.details:
            for key, value in ctx.details.items():
                self._echo(f"  - {key}: {value}")

        while True:
            answer = self._ask("¿Aprobar? [s/n/cambios] ").strip().lower()
            if answer in ("s", "si", "sí", "y", "yes"):
                return ApprovalDecision(status=ApprovalStatus.APPROVED, reason="aprobado por el usuario")
            if answer in ("n", "no"):
                return ApprovalDecision(
                    status=ApprovalStatus.REJECTED, reason="rechazado por el usuario"
                )
            if answer in ("c", "cambios", "cambiar"):
                feedback = self._ask("¿Qué hay que cambiar? ").strip()
                return ApprovalDecision(
                    status=ApprovalStatus.NEEDS_CHANGES, feedback=feedback
                )
            self._echo("responde 's', 'n' o 'cambios'.")
