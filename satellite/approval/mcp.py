"""Aprobación desde MCP: se **pregunta al cliente**, nunca se aprueba solo.

La elicitación es una capacidad del **cliente** (spec 2025-06-18: «los clientes que
la soportan DEBEN declarar `elicitation` al inicializar»). El servidor no la
declara: solo la lee y, si el cliente la ofrece, envía `elicitation/create`. Si no
la ofrece, no responde o responde que no, se **rechaza** con el mensaje de siempre.

Además, solo las propuestas de **bajo riesgo** se pueden aprobar por esta vía
(crear un agente o skill sin red, sin shell y sin escrituras fuera del workspace).
Las de alto riesgo —red, shell, subprocesos, delegación, escrituras— se resuelven
solo en el CLI o en la TUI: aquí se informan y se rechazan.

Ojo: se **solicita** confirmación al cliente; no se garantiza que haya un humano
detrás (el cliente puede auto-responder). Eso es responsabilidad suya.
"""

from __future__ import annotations

import asyncio

from satellite.approval.base import ApprovalContext, ApprovalPort, proposal_id_of
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.retrieval.sanitize import sanitize

DENIED_REASON = (
    "requiere decisión humana: apruébala en el CLI o en la TUI (MCP no aprueba)"
)
UNAVAILABLE_REASON = (
    f"{DENIED_REASON} (el cliente no declara soporte de elicitation)"
)
ELICITATION_TIMEOUT_S = 120.0
MESSAGE_MAX_CHARS = 300

# Esquema de la elicitación: objeto **plano** de primitivos (lo que la spec admite).
APPROVAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "approve": {
            "type": "boolean",
            "title": "Aprobar",
            "description": "true para aprobar la propuesta, false para rechazarla.",
            "default": False,
        }
    },
    "required": ["approve"],
}

# Señales de riesgo: permisos declarados o tools que implican efectos fuera del
# control del framework (por eso no se aprueban sin un humano en el CLI/TUI).
_RISKY_KEYS: tuple[str, ...] = (
    "network",
    "write",
    "write_files",
    "filesystem_write",
    "shell",
    "terminal",
    "process",
    "subprocess",
    "delegate",
)
_RISKY_VALUES: tuple[str, ...] = (
    "run_argv",
    "run_command",
    "run_python",
    "write_file",
    "edit_file",
    "delegate",
    "spawn_agent",
)
_FALSEY: tuple[object, ...] = (False, None, 0, "", (), [], {})
# Nunca se manda por elicitación nada que parezca un secreto (la spec lo prohíbe).
_SECRET_HINTS: tuple[str, ...] = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "auth",
)


def risky_reasons(proposal: object, ctx: ApprovalContext) -> list[str]:
    """Motivos por los que la propuesta **no** se aprueba por elicitación."""
    reasons: list[str] = []
    if str(ctx.kind) not in ("agent", "skill"):
        reasons.append(str(ctx.kind))
    haystack: list[str] = []
    for source in (getattr(ctx, "details", None), _proposal_dict(proposal)):
        if isinstance(source, dict):
            for key, value in source.items():
                if value in _FALSEY:
                    continue
                if str(key).lower() in _RISKY_KEYS:
                    reasons.append(str(key))
                haystack.extend(_strings(value))
    for risky in _RISKY_VALUES:
        if any(risky == item or item.startswith(f"{risky}?") for item in haystack):
            reasons.append(risky)
    return sorted(set(reasons))


def _proposal_dict(proposal: object) -> dict:
    if isinstance(proposal, dict):
        return proposal
    to_dict = getattr(proposal, "to_dict", None)
    return to_dict() if callable(to_dict) else {}


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return []


def approval_message(proposal_id: str, summary: str, details: dict | None = None) -> str:
    """Mensaje **estructurado**, truncado y saneado (nunca claves ni contenido)."""
    lines = [
        f"Aprobar la propuesta {proposal_id}.",
        f"Resumen: {sanitize(summary, max_chars=MESSAGE_MAX_CHARS)}",
    ]
    if isinstance(details, dict):
        safe = {
            str(key): sanitize(value, max_chars=120)
            for key, value in sorted(details.items())
            if isinstance(value, str) and not _looks_secret(str(key))
        }
        if safe:
            lines.append("Datos: " + ", ".join(f"{k}={v}" for k, v in list(safe.items())[:6]))
    lines.append("Responde con `approve`.")
    return "\n".join(lines)


def _looks_secret(key: str) -> bool:
    """True si el nombre del campo sugiere un secreto (no se envía nunca)."""
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


class McpApproval(ApprovalPort):
    """Pregunta al cliente (si puede); si no, rechaza. Nunca aprueba sola."""

    def __init__(
        self,
        elicitation=None,
        *,
        timeout_s: float = ELICITATION_TIMEOUT_S,
    ) -> None:
        # `elicitation(message, schema, timeout) -> {"action": ...}` (lo pone el server).
        self.elicitation = elicitation
        self.timeout_s = float(timeout_s)
        self._awaiting: list[dict] = []

    async def request(self, proposal: object, ctx: ApprovalContext) -> ApprovalDecision:
        """Pide confirmación al cliente y decide; fail-closed en todos los casos."""
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
        reasons = risky_reasons(proposal, ctx)
        if reasons:
            return ApprovalDecision(
                status=ApprovalStatus.REJECTED,
                reason=f"{DENIED_REASON} (riesgo: {', '.join(reasons)})",
            )
        if self.elicitation is None:
            return ApprovalDecision(status=ApprovalStatus.REJECTED, reason=UNAVAILABLE_REASON)

        result = await self._ask(approval_message(proposal_id, ctx.summary, ctx.details))
        if result.get("action") != "accept":
            # decline, cancel, timeout o error: se rechaza sin más.
            return ApprovalDecision(status=ApprovalStatus.REJECTED, reason=DENIED_REASON)
        content = result.get("content")
        approved = bool(content.get("approve")) if isinstance(content, dict) else False
        if not approved:
            return ApprovalDecision(status=ApprovalStatus.REJECTED, reason=DENIED_REASON)
        return ApprovalDecision(
            status=ApprovalStatus.APPROVED, reason="aprobado por el cliente (elicitation)"
        )

    async def _ask(self, message: str) -> dict:
        """Llama al canal del servidor con vencimiento; nunca lanza."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.elicitation, message, APPROVAL_SCHEMA, self.timeout_s
                ),
                timeout=self.timeout_s + 5,
            )
        except Exception:  # noqa: BLE001 — cualquier fallo es «sin decisión»
            return {"action": "timeout"}

    def pending(self) -> list[dict]:
        """Propuestas esperando una decisión humana (solo informativo)."""
        return [dict(item) for item in self._awaiting]
