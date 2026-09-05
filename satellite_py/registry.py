"""Python registry for Satellite agent descriptors."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentDescriptor:
    """Metadata needed to route a request to an isolated agent host."""

    id: int = 0
    name: str = ""
    description: str = ""
    version: str = "0.1.0"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    context_requirements: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    library_path: str = ""
    enabled: bool = True
    # Complementos: capabilities de agentes a los que este agente pasa su
    # output DIRECTAMENTE (sin volver al orquestador) cuando tiene éxito.
    # Vacío = agente hoja (el resultado vuelve al orquestador).
    complements: list[str] = field(default_factory=list)


class AgentRegistry:
    """Registry of agent descriptors keyed by numeric agent ID."""

    def __init__(self) -> None:
        self._agents: dict[int, AgentDescriptor] = {}

    def register_agent(self, descriptor: AgentDescriptor) -> bool:
        """Register an enabled agent, rejecting ID 0 and duplicate IDs."""
        if descriptor.id == 0 or descriptor.id in self._agents:
            return False
        self._agents[descriptor.id] = descriptor
        return True

    def find_agent(self, agent_id: int) -> AgentDescriptor | None:
        """Return a descriptor by ID, or None when it is not registered."""
        return self._agents.get(agent_id)

    def set_enabled(self, agent_id: int, enabled: bool) -> bool:
        """Enable/disable an agent (web UI "bloquear"); False on unknown ID."""
        descriptor = self._agents.get(agent_id)
        if descriptor is None:
            return False
        descriptor.enabled = enabled
        return True

    def list_agents(self) -> list[AgentDescriptor]:
        """Return all registered descriptors in ascending ID order."""
        return [self._agents[agent_id] for agent_id in sorted(self._agents)]
