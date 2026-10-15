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


class ProviderError(SatelliteError):
    """Error de un proveedor LLM."""


class ContextError(SatelliteError):
    """Error de contexto."""
