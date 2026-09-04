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

    if descriptor.library_path:
        result = agent_host_bridge.run_agent(
            descriptor.library_path,
            request,
            agent_host_bin=agent_host_bin,
        )
    else:
        # Sin library_path: agente nativo 1-5 (in-process en C++) o descriptor
        # sin plugin. Se delega la ejecución al binario C++ (dispatch-step),
        # que registra nativos + registry y reconstruye agentes custom.
        from .runtime.cpp_cli_bridge import available, run_cpp_json

        if not available():
            return {
                "agent_id": agent_id,
                "status": "FAILED",
                "error": (
                    f"agent {agent_id} no tiene library_path y el binario C++ "
                    "(build/satellite) no está disponible para ejecutarlo"
                ),
            }
        try:
            cpp_result = run_cpp_json(
                ["dispatch-step"],
                {
                    "agent_id": agent_id,
                    "input": request.get("input") or {},
                    "context": request.get("context") or {},
                },
                timeout=120,
            )
        except Exception as error:  # noqa: BLE001 - traducir a resultado
            return {"agent_id": agent_id, "status": "FAILED", "error": str(error)}
        result = {
            "agent_id": agent_id,
            "status": "SUCCESS" if cpp_result.get("ok") else "FAILED",
            "output": cpp_result.get("output"),
            "error": cpp_result.get("error"),
        }
    if result.get("status") == "SUCCESS":
        output_error = validate_output(result.get("output"), descriptor.output_schema)
        if output_error is not None:
            return {
                "agent_id": agent_id,
                "status": "VALIDATION_ERROR",
                "error": output_error,
            }
    return result