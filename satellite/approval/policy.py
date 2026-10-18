"""Motor determinista de políticas: ask / auto / unrestricted."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from satellite.approval.base import ApprovalContext, ApprovalPort
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.kernel.policy import PolicyMode, Quotas

HOUR_SECONDS = 3600.0


def activate_unrestricted(confirmed: bool) -> PolicyMode:
    """Devuelve UNRESTRICTED sólo con confirmación explícita; si no, ASK."""
    return PolicyMode.UNRESTRICTED if confirmed else PolicyMode.ASK


@dataclass
class ProposalCounter:
    """Cuenta propuestas por tarea y por hora (cortacircuitos anti-spam)."""

    quotas: Quotas = field(default_factory=Quotas)
    clock: Callable[[], float] = time.monotonic
    _by_task: dict[str, int] = field(default_factory=dict)
    _recent: list[float] = field(default_factory=list)

    def register(self, task_id: str) -> None:
        """Registra una propuesta emitida."""
        self._by_task[task_id] = self._by_task.get(task_id, 0) + 1
        self._recent.append(self.clock())

    def task_count(self, task_id: str) -> int:
        """Propuestas emitidas en esa tarea."""
        return self._by_task.get(task_id, 0)

    def hour_count(self) -> int:
        """Propuestas emitidas en la última hora (poda las antiguas)."""
        cutoff = self.clock() - HOUR_SECONDS
        self._recent = [stamp for stamp in self._recent if stamp >= cutoff]
        return len(self._recent)

    def within_quotas(self, task_id: str) -> bool:
        """True si aún se puede emitir otra propuesta."""
        if self.task_count(task_id) >= self.quotas.proposals_per_task:
            return False
        if self.hour_count() >= self.quotas.proposals_per_hour:
            return False
        return True


class ApprovalPolicy:
    """Decide si una propuesta se aprueba sin preguntar o requiere al usuario."""

    def __init__(
        self,
        mode: PolicyMode = PolicyMode.ASK,
        *,
        counter: ProposalCounter | None = None,
    ) -> None:
        self.mode = mode
        self.counter = counter or ProposalCounter()

    def pre_decide(
        self,
        *,
        reserved: bool = False,
        quota_ok: bool = True,
        validation_ok: bool = True,
        requires_human: bool = False,
    ) -> ApprovalDecision | None:
        """Decisión determinista previa; ``None`` = hay que preguntar.

        Los ids reservados y las cuotas se rechazan SIEMPRE (ni ``unrestricted``
        los salta). ``auto`` sólo aprueba si la validación pasó. ``requires_human``
        (p. ej. control de escritorio) **nunca** se autoaprueba: ni ``auto`` ni
        ``unrestricted`` la saltan; sin puerto se rechaza en ``decide``.
        """
        if reserved:
            return ApprovalDecision(
                status=ApprovalStatus.REJECTED, reason="id reservado por el kernel"
            )
        if not quota_ok:
            return ApprovalDecision(
                status=ApprovalStatus.REJECTED, reason="cuota de propuestas excedida"
            )
        if requires_human:
            return None
        if self.mode is PolicyMode.AUTO:
            if validation_ok:
                return ApprovalDecision(
                    status=ApprovalStatus.APPROVED, reason="auto: validación y sandbox OK"
                )
            return ApprovalDecision(
                status=ApprovalStatus.REJECTED, reason="auto: la validación falló"
            )
        if self.mode is PolicyMode.UNRESTRICTED:
            return ApprovalDecision(status=ApprovalStatus.APPROVED, reason="unrestricted")
        return None

    async def decide(
        self,
        proposal: object,
        ctx: ApprovalContext,
        port: ApprovalPort | None,
        *,
        reserved: bool = False,
        quota_ok: bool = True,
        validation_ok: bool = True,
        requires_human: bool = False,
    ) -> ApprovalDecision:
        """Aplica la política y, si toca, delega la pregunta en el puerto."""
        forced = self.pre_decide(
            reserved=reserved,
            quota_ok=quota_ok,
            validation_ok=validation_ok,
            requires_human=requires_human,
        )
        if forced is not None:
            return forced
        if port is None:
            return ApprovalDecision(
                status=ApprovalStatus.REJECTED, reason="no hay puerto de aprobación"
            )
        return await port.request(proposal, ctx)
