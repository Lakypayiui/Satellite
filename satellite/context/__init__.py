"""Motor de contexto just-in-time."""

from satellite.context.base import ContextEngine
from satellite.context.engine import DefaultContextEngine
from satellite.context.project_index import ProjectIndex
from satellite.context.resolver import ContextResolver
from satellite.context.preprocessor import LocalPreprocessor
from satellite.context.optimizer import ContextSelection, DefaultContextOptimizer, OptimizationStats

__all__ = [
    "ContextEngine",
    "DefaultContextEngine",
    "ProjectIndex",
    "ContextResolver",
    "LocalPreprocessor",
    "ContextSelection",
    "DefaultContextOptimizer",
    "OptimizationStats",
]
