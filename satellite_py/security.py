"""Capability policy for Python-side Satellite orchestration."""

from collections.abc import Mapping

from .registry import AgentDescriptor


class SecurityPolicy:
    """Deny-by-default capability policy for agent descriptors."""

    NO_CAPABILITIES_CAPABILITY = "agent.no_capabilities"

    def __init__(self, allow_map: Mapping[str, bool] | None = None) -> None:
        self.allow_map: dict[str, bool] = dict(allow_map or {})

    def set_allowed(self, capability: str, allowed: bool) -> None:
        self.allow_map[capability] = allowed

    def is_allowed(self, capability: str) -> bool:
        return self.allow_map.get(capability, False)

    def load_defaults(self) -> None:
        self.allow_map.update(
            {
                "filesystem.read": True,
                "filesystem.write": False,
                "process.execute": False,
                "compiler.execute": False,
                "network.request": False,
            }
        )

    def from_config(self, allow_map: Mapping[str, bool]) -> None:
        self.allow_map = dict(allow_map)

    def validate_agent(self, descriptor: AgentDescriptor) -> tuple[bool, str | None]:
        """Return (allowed, denied capability) for a descriptor."""
        if not descriptor.capabilities:
            if self.is_allowed(self.NO_CAPABILITIES_CAPABILITY):
                return True, None
            return False, self.NO_CAPABILITIES_CAPABILITY

        for capability in descriptor.capabilities:
            if not self.is_allowed(capability):
                return False, capability
        return True, None
