"""Coordinación global: el orquestador piloto y la delegación entre agentes."""

from satellite.orchestration.base import Orchestrator, RuntimeServices
from satellite.orchestration.delegation import (
    DelegationContext,
    DelegationManager,
    DelegationResult,
)
from satellite.orchestration.pilot import PilotOrchestrator

__all__ = [
    "RuntimeServices",
    "Orchestrator",
    "DelegationContext",
    "DelegationManager",
    "DelegationResult",
    "PilotOrchestrator",
]
