"""Jerarquía de errores de Satellite 2.0."""


class SatelliteError(Exception):
    """Base de todos los errores del framework."""


class ContractError(SatelliteError):
    """Violación de contrato o modelo."""


class SpecValidationError(ContractError):
    """Un AgentSpec inválido."""


class GraphValidationError(ContractError):
    """Un ExecutionGraph inválido."""


class ToolError(SatelliteError):
    """Error durante la ejecución de una tool."""


class PathOutsideProject(ToolError):
    """La tool pidió una ruta fuera del proyecto.

    Es denegación **autorizable**: el runtime puede pedir permiso al usuario y
    reintentar. Lleva la ruta ya resuelta y el ``project_root`` para construir la
    petición sin tener que parsear el mensaje.
    """

    code = "path_outside_project"

    def __init__(self, resolved, project_root, raw: str = "") -> None:
        self.resolved = resolved
        self.project_root = project_root
        self.raw = raw
        # Viajan en el ToolResult para que quien pregunte no tenga que parsear texto.
        self.details = {
            "path": str(resolved),
            "project_root": str(project_root),
            "raw": str(raw),
        }
        super().__init__(
            f"fuera del proyecto: {resolved} (el proyecto es {project_root})"
        )


class ProtectedZoneForbidden(ToolError):
    """La tool tocaba una zona interna del framework (kernel, estado, auditoría, config).

    A diferencia de ``PathOutsideProject``, esto **no** es autorizable: ninguna
    aprobación puede saltarlo.
    """

    code = "protected_zone"


class ProviderError(SatelliteError):
    """Error de un proveedor LLM."""


class ContextError(SatelliteError):
    """Error de contexto."""
