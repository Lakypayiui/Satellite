"""Validadores de resultados de nodos."""

from satellite.validation.base import CheckResult, Validator, ValidationContext
from satellite.validation.checks import FileExistsCheck, JsonSchemaOutputCheck

__all__ = ["CheckResult", "Validator", "ValidationContext", "FileExistsCheck", "JsonSchemaOutputCheck"]
