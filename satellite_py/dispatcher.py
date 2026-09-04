"""Python dispatcher for isolated Satellite agent execution."""

from typing import Any

from .registry import AgentRegistry
from .runtime import agent_host_bridge
from .security import SecurityPolicy
from .validation import validate_input, validate_output


def dispatch(
    registry: AgentRegistry,
    security: SecurityPolicy,
    request: dict[str, Any],
    agent_host_bin: str = "./build/satellite_agent_host",
) -> dict[str, Any]:
    """Validate routing and execute an agent through the isolated host."""
    agent_id = request.get("agent_id")
    descriptor = registry.find_agent(agent_id)
    if descriptor is None:
        return {
            "agent_id": agent_id,
            "status": "UNKNOWN_AGENT",
            "error": f"unknown agent id: {agent_id}",
        }

    if not descriptor.enabled:
        return {
            "agent_id": agent_id,
            "status": "DISABLED",
            "error": f"agent disabled: {agent_id}",
        }

    allowed, denied_capability = security.validate_agent(descriptor)
    if not allowed:
        return {
            "agent_id": agent_id,
            "status": "SECURITY_DENIED",
            "denied_capability": denied_capability,
        }

    input_error = validate_input(request.get("input"), descriptor.input_schema)
    if input_error is not None:
        return {
            "agent_id": agent_id,
            "status": "VALIDATION_ERROR",
            "error": input_error,
        }

    result = agent_host_bridge.run_agent(
        descriptor.library_path,
        request,
        agent_host_bin=agent_host_bin,
    )
    if result.get("status") == "SUCCESS":
        output_error = validate_output(result.get("output"), descriptor.output_schema)
        if output_error is not None:
            return {
                "agent_id": agent_id,
                "status": "VALIDATION_ERROR",
                "error": output_error,
            }
    return result