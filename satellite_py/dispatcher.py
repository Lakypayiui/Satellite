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
    auto_expand: bool = False,
    goal: str = "",
    expand_client: Any = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Validate routing and execute an agent through the isolated host.

    ``auto_expand``: cuando el agente pedido no existe (y no es un nativo
    1-5), crearlo bajo demanda (spec generada con el proveedor configurado
    ``expand_client`` → compilar → tests → registrar) y reintentar el paso.
    ``goal``: objetivo de la corrida, que da contexto al generador.
    ``project_root``: directorio del proyecto (la factory escribe la .dll ahí).
    """
    agent_id = request.get("agent_id")
    descriptor = registry.find_agent(agent_id)

    # Los agentes nativos 1-5 viven in-process en el C++; si el registry
    # Python no los tiene (p.ej. proyecto inicializado solo con el init
    # Python, sin sembrar los descriptores), se delega igualmente al C++
    # (dispatch-step registra nativos + registry y reconstruye agentes).
    if descriptor is None and isinstance(agent_id, int) and 1 <= agent_id <= 5:
        from .runtime.cpp_cli_bridge import available, run_cpp_json

        if not available():
            return {
                "agent_id": agent_id,
                "status": "FAILED",
                "error": (
                    f"agente nativo {agent_id} no registrado en el registry y "
                    "el binario C++ no está disponible para ejecutarlo"
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
        return {
            "agent_id": agent_id,
            "status": "SUCCESS" if cpp_result.get("ok") else "FAILED",
            "output": cpp_result.get("output"),
            "error": cpp_result.get("error"),
        }

    if descriptor is None:
        # Auto-expansión bajo demanda: el run pide un agente que no existe.
        if auto_expand and not (isinstance(agent_id, int) and 1 <= agent_id <= 5):
            capability = _capability_from_request(request)
            try:
                from .expander import AgentExpander

                expander = AgentExpander(registry, client=expand_client, cwd=project_root)
                created = expander.expand(goal or request.get("metadata", {}).get("goal", ""), capability)
            except Exception as error:  # noqa: BLE001 - la generación falló
                return {
                    "agent_id": agent_id,
                    "status": "EXPANSION_FAILED",
                    "error": f"auto-expansión de '{capability}' falló: {error}",
                    "capability": capability,
                }
            descriptor = registry.find_agent(created.id)
            if descriptor is None:
                return {
                    "agent_id": agent_id,
                    "status": "EXPANSION_FAILED",
                    "error": f"auto-expansión registró {created.id} pero no se resolvió",
                    "capability": capability,
                }
        else:
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
        # Sandbox de efectos de sistema: se autorizan solo las capacidades
        # permitidas y se acota el write al workspace del proyecto.
        if project_root:
            request = {**request, "sandbox": _build_sandbox(security, str(project_root))}
        try:
            result = agent_host_bridge.run_agent(
                descriptor.library_path,
                request,
                agent_host_bin=agent_host_bin,
            )
        except Exception as error:  # noqa: BLE001 - traducir a resultado
            return {
                "agent_id": agent_id,
                "status": "FAILED",
                "error": str(error),
            }
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
            cpp_request = {
                "agent_id": agent_id,
                "input": request.get("input") or {},
                "context": request.get("context") or {},
            }
            # Sandbox de efectos: el C++ cmd_dispatch_step lo reconstruye y lo
            # pasa al agente. Sin él, un agente con efectos queda en cómputo puro.
            if project_root:
                cpp_request["sandbox"] = _build_sandbox(security, str(project_root))
            cpp_result = run_cpp_json(
                ["dispatch-step"],
                cpp_request,
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


def _capability_from_request(request: dict[str, Any]) -> str:
    """Deriva la capability/descripción del paso para la auto-expansión."""
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    if metadata.get("capability"):
        return str(metadata["capability"])
    description = metadata.get("description") or request.get("description") or ""
    if description:
        return str(description)
    agent_id = request.get("agent_id")
    return f"agente para id {agent_id}" if agent_id is not None else "agente faltante"


def _build_sandbox(security: SecurityPolicy, project_root: str) -> dict[str, Any]:
    """Construye el sandbox de efectos acotado al workspace del proyecto.

    El work_dir es el PROYECTO COMPLETO: el subagente puede editar cualquier
    archivo del repo (crear/escribir/leer), ejecutar procesos (compilar, tests,
    git) y hacer requests de red — según lo que permita el SecurityPolicy.
    La escritura dentro de ``.satellite/`` queda prohibida (el runtime la
    rechaza además del límite por work_dir).
    """
    import os

    workspace = os.path.abspath(os.fspath(project_root))
    return {
        "work_dir": workspace,
        "deny_write_prefixes": [os.path.join(workspace, ".satellite")],
        "allow_fs_write": security.is_allowed("filesystem.write"),
        "allow_fs_read": security.is_allowed("filesystem.read"),
        "allow_process": security.is_allowed("process.execute"),
        "allow_network": security.is_allowed("network.request"),
    }