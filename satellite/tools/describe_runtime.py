"""Tool `describe_runtime`: el piloto consulta el propio sistema desde sus registros.

Preguntas como «¿qué agentes tienes?», «¿qué skills?», «¿qué herramientas?» o «¿qué
modelo eres?» se responden con lo que hay cargado de verdad (agentes, skills, tools y
modelo), no con lo que el modelo imagine. Sustituye a la respuesta local por regex del
modo antiguo (docs/PLAN-flujo-piloto.md, fase 6).
"""

from __future__ import annotations

from typing import Callable

from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext

TOPICS = ("agents", "skills", "tools", "model")


class DescribeRuntimeTool(Tool):
    """Lista agentes, skills, tools o el modelo del runtime en curso."""

    def __init__(self, services: Callable[[], object | None] | None = None) -> None:
        # Los servicios se construyen después que el registro de tools: se leen al
        # ejecutar, no al registrar.
        self._services = services or (lambda: None)

    def bind(self, services: Callable[[], object | None]) -> None:
        self._services = services

    @property
    def id(self) -> str:
        return "describe_runtime"

    @property
    def description(self) -> str:
        return (
            "Describes this assistant itself from its live registries: `topic` is one "
            "of agents, skills, tools or model. Use it when the user asks what agents, "
            "skills, tools or model you have; answer from its output."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("conversation",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": list(TOPICS)}},
            "required": ["topic"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        topic = str((arguments or {}).get("topic") or "").strip().lower()
        if topic not in TOPICS:
            return ToolResult(
                success=False, error=f"topic debe ser uno de: {', '.join(TOPICS)}"
            )
        services = self._services()
        if services is None:
            return ToolResult(success=False, error="este runtime no expone sus registros")
        return ToolResult(success=True, output=describe(topic, services))


def describe(topic: str, services: object) -> dict:
    """Lo que hay cargado para ``topic`` (``agents``, ``skills``, ``tools`` o ``model``)."""
    if topic == "agents":
        registry = getattr(services, "registry", None)
        specs = sorted(registry.all(), key=lambda spec: spec.id) if registry else []
        return {
            "agents": [
                {
                    "id": spec.id,
                    "description": spec.description or spec.name,
                    "capabilities": list(spec.capabilities),
                }
                for spec in specs
            ],
            "note": "Kernel tools (read_file, run_command…) are tools, not agents.",
        }
    if topic == "skills":
        registry = getattr(services, "skills", None)
        if registry is None:
            return {"skills": [], "note": "No skill catalog in this runtime."}
        return {
            "skills": [
                {
                    "id": manifest.id,
                    "description": manifest.description,
                    "deterministic": bool(getattr(manifest, "deterministic", False)),
                }
                for manifest in sorted(registry.all(), key=lambda item: item.id)
            ],
            "note": "Kernel primitives are tools, not skills.",
        }
    if topic == "tools":
        registry = getattr(services, "tools", None)
        tools = sorted(registry.all(), key=lambda item: item.id) if registry else []
        return {"tools": [{"id": tool.id, "description": tool.description} for tool in tools]}
    return _model(services)


def _model(services: object) -> dict:
    provider = getattr(services, "provider", None)
    if provider is None:
        return {"provider": "", "model": "", "note": "No LLM provider configured."}
    # El proveedor llega envuelto (medición, reintentos, proxy): se busca el modelo
    # en la cadena y el nombre en el envoltorio que lo declara.
    model = ""
    name = ""
    current: object | None = provider
    while current is not None:
        model = model or str(getattr(current, "model", "") or "")
        name = name or str(getattr(current, "provider_name", "") or "")
        current = getattr(current, "inner", None)
    return {"provider": name or type(provider).__name__, "model": model}


__all__ = ["DescribeRuntimeTool", "TOPICS", "describe"]
