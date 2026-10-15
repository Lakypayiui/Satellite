"""Propuestas pendientes de decisión, resumibles por task_id."""

from __future__ import annotations

from satellite.core.proposal import ApprovalDecision


class PendingProposals:
    """Registro en memoria de propuestas pendientes (serializable)."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def add(self, proposal: object, *, task_id: str = "") -> str:
        """Registra una propuesta y devuelve su id."""
        if isinstance(proposal, dict):
            data: dict = dict(proposal)
        elif hasattr(proposal, "to_dict"):
            data = proposal.to_dict()
        else:
            data = {"repr": repr(proposal)}
        proposal_id = str(
            getattr(proposal, "proposal_id", "")
            or data.get("proposal_id")
            or f"prop{len(self._items) + 1}"
        )
        self._items[proposal_id] = {
            "proposal_id": proposal_id,
            "task_id": task_id,
            "proposal": data,
            "status": "pending",
            "decision": None,
        }
        return proposal_id

    def get(self, proposal_id: str) -> dict | None:
        """Devuelve la entrada o None."""
        return self._items.get(str(proposal_id))

    def for_task(self, task_id: str) -> list[dict]:
        """Propuestas pendientes de una tarea."""
        return [
            item
            for item in self._items.values()
            if item["task_id"] == task_id and item["status"] == "pending"
        ]

    def all(self) -> list[dict]:
        """Todas las entradas."""
        return list(self._items.values())

    def resolve(self, proposal_id: str, decision: ApprovalDecision) -> dict | None:
        """Marca una propuesta como resuelta."""
        item = self._items.get(str(proposal_id))
        if item is None:
            return None
        item["status"] = decision.status.value
        item["decision"] = decision.to_dict()
        return item

    def to_dict(self) -> dict:
        """Serializa el registro (para el store)."""
        return {"items": list(self._items.values())}

    @classmethod
    def from_dict(cls, d: dict | None) -> "PendingProposals":
        """Reconstruye el registro desde un dict."""
        registry = cls()
        for item in (d or {}).get("items", []):
            registry._items[str(item["proposal_id"])] = item
        return registry
