"""Contrato del puerto de aprobación y sus implementaciones básicas.

``ask``/``auto``/``unrestricted`` se deciden en ``policy.py``; el puerto sólo
transporta la pregunta al usuario y devuelve su decisión. Así CLI, Web y MCP
comparten exactamente la misma lógica.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from satellite.core.proposal import ApprovalDecision, ApprovalStatus


@dataclass
class ApprovalContext:
    """Contexto de una petición de aprobación."""

    kind: str = "agent"  # agent | skill | edit | delegation
    task_id: str = ""
    proposal_id: str = ""
    summary: str = ""
    mode: str = "ask"
    details: dict = field(default_factory=dict)


def proposal_id_of(proposal: object, ctx: ApprovalContext) -> str:
    """Id estable de una propuesta (para correlacionar la decisión)."""
    value = getattr(proposal, "proposal_id", None)
    if value:
        return str(value)
    if isinstance(proposal, dict) and proposal.get("proposal_id"):
        return str(proposal["proposal_id"])
    return ctx.proposal_id or f"prop{abs(hash(repr(proposal))) & 0xFFFFFF:06x}"


class ApprovalPort(ABC):
    """Pide al usuario que apruebe/rechace una propuesta."""

    @abstractmethod
    async def request(self, proposal: object, ctx: ApprovalContext) -> ApprovalDecision:
        """Devuelve la decisión del usuario sobre la propuesta."""
        raise NotImplementedError


class AutoApprovalPort(ApprovalPort):
    """Aprueba siempre (headless/tests)."""

    async def request(self, proposal: object, ctx: ApprovalContext) -> ApprovalDecision:
        return ApprovalDecision(status=ApprovalStatus.APPROVED, reason="auto")


class DenyAllApprovalPort(ApprovalPort):
    """Rechaza siempre (headless/tests)."""

    async def request(self, proposal: object, ctx: ApprovalContext) -> ApprovalDecision:
        return ApprovalDecision(
            status=ApprovalStatus.REJECTED, reason="denegado por política"
        )


class PendingApprovalPort(ApprovalPort):
    """Puerto asíncrono: ``request`` queda a la espera de ``submit``.

    Base para Web (modal + polling) y MCP (tool ``satellite_approve``): quien
    responde llama a ``submit`` con la decisión y el ``await`` se resuelve.
    """

    def __init__(self, timeout_s: float | None = None) -> None:
        self.timeout_s = timeout_s
        self._pending: dict[str, dict] = {}
        self._decisions: dict[str, ApprovalDecision] = {}
        self._events: dict[str, asyncio.Event] = {}

    async def request(self, proposal: object, ctx: ApprovalContext) -> ApprovalDecision:
        proposal_id = proposal_id_of(proposal, ctx)
        if proposal_id in self._decisions:
            return self._decisions.pop(proposal_id)

        event = asyncio.Event()
        self._events[proposal_id] = event
        self._pending[proposal_id] = {
            "proposal_id": proposal_id,
            "kind": ctx.kind,
            "task_id": ctx.task_id,
            "summary": ctx.summary,
            "details": dict(ctx.details),
        }
        try:
            if self.timeout_s is None:
                await event.wait()
            else:
                await asyncio.wait_for(event.wait(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            self._forget(proposal_id)
            return ApprovalDecision(
                status=ApprovalStatus.REJECTED, reason="timeout de aprobación"
            )

        decision = self._decisions.pop(
            proposal_id,
            ApprovalDecision(status=ApprovalStatus.REJECTED, reason="sin decisión"),
        )
        self._forget(proposal_id)
        return decision

    def submit(self, proposal_id: str, decision: ApprovalDecision) -> bool:
        """Registra la decisión; True si había una petición esperándola."""
        key = str(proposal_id)
        self._decisions[key] = decision
        event = self._events.get(key)
        if event is not None:
            event.set()
            return True
        return False

    def pending(self) -> list[dict]:
        """Propuestas esperando decisión."""
        return list(self._pending.values())

    def is_pending(self, proposal_id: str) -> bool:
        """True si esa propuesta está esperando decisión."""
        return str(proposal_id) in self._pending

    def _forget(self, proposal_id: str) -> None:
        self._pending.pop(proposal_id, None)
        self._events.pop(proposal_id, None)
