"""Python-side agent expansion through the factory process bridge."""

from typing import Any

from .registry import AgentDescriptor, AgentRegistry
from .runtime.factory_bridge import create_agent


class AgentExpander:
    """Request missing capabilities and register returned descriptors."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    @staticmethod
    def build_prompt(goal: str, capability: str) -> str:
        return (
            "Genera la especificación de un microagente C++ para Satellite. "
            f"Objetivo: {goal}. Capacidad requerida: {capability}. "
            'Responde SOLO con JSON: {"name": "...", "description": "...", '
            '"input_schema": {...}, "output_schema": {...}, '
            '"implementation_code": "...", "test_cases": '
            '[{"input": {...}, "expected": {...}}]}. '
            'El implementation_code es una clase que implementa '
            'satellite::core::agent::IAgent (método execute(const AgentRequest&)) '
            'y exporta las funciones extern "C" IAgent* satellite_create_agent() '
            'y void satellite_destroy_agent(IAgent*). '
            'El JSON del implementation_code debe tener los saltos de línea '
            'como \\n escapados.'
        )

    def expand(self, goal: str, capability: str) -> AgentDescriptor:
        """Compile one capability through satellite_factory_cli and register it."""
        response = create_agent(goal, capability)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "agent expansion failed"))

        payload = response.get("descriptor")
        if not isinstance(payload, dict):
            raise RuntimeError("factory response has no descriptor")

        descriptor = AgentDescriptor(
            id=payload["id"],
            name=payload.get("name", ""),
            description=payload.get("description", ""),
            version=payload.get("version", "0.1.0"),
            input_schema=payload.get("input_schema", {}),
            output_schema=payload.get("output_schema", {}),
            context_requirements=payload.get("context_requirements", []),
            capabilities=payload.get("capabilities", [capability]),
            library_path=payload["library_path"],
        )
        if not self.registry.register_agent(descriptor):
            raise RuntimeError(f"agent id already registered: {descriptor.id}")
        return descriptor
