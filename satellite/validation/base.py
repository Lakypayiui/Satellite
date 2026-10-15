"""Interfaz de validadores de resultados."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from satellite.core.result import AgentResult


@dataclass(frozen=True)
class CheckResult:
    """Resultado de una validación."""

    validator_id: str
    ok: bool
    detail: str = ""


@dataclass
class ValidationContext:
    """Contexto de validación."""

    project_root: Path
    workdir: Path | None = None


class Validator(ABC):
    """Valida el resultado de un nodo (p. ej. HTML sintácticamente válido)."""

    @abstractmethod
    async def validate(
        self, result: AgentResult, vctx: ValidationContext
    ) -> CheckResult:
        """Valida el resultado y devuelve CheckResult."""
        raise NotImplementedError
