"""Validadores deterministas (nivel 1)."""

from __future__ import annotations

from pathlib import Path

from satellite.core.result import AgentResult
from satellite.validation.base import CheckResult, ValidationContext, Validator


class FileExistsCheck(Validator):
    """Verifica que el archivo indicado en output.path existe."""

    def __init__(self, validator_id: str = "file_exists") -> None:
        self.validator_id = validator_id

    async def validate(
        self, result: AgentResult, vctx: ValidationContext
    ) -> CheckResult:
        path_value = result.output.get("path") if isinstance(result.output, dict) else None
        if not isinstance(path_value, str) or not path_value:
            return CheckResult(
                validator_id=self.validator_id,
                ok=False,
                detail="output no contiene 'path' (string)",
            )
        target = Path(vctx.project_root, path_value)
        if target.exists():
            return CheckResult(validator_id=self.validator_id, ok=True)
        return CheckResult(
            validator_id=self.validator_id,
            ok=False,
            detail=f"archivo no existe: {target}",
        )


class JsonSchemaOutputCheck(Validator):
    """Valida result.output contra un JSON Schema."""

    def __init__(self, schema: dict | None = None, validator_id: str = "json_schema_output") -> None:
        self.schema = schema
        self.validator_id = validator_id

    async def validate(
        self, result: AgentResult, vctx: ValidationContext
    ) -> CheckResult:
        schema = self.schema
        if schema is None:
            schema = getattr(result, "output_schema", None)
        if schema is None:
            return CheckResult(
                validator_id=self.validator_id,
                ok=True,
                detail="sin schema, validación omitida",
            )
        try:
            import jsonschema

            jsonschema.validate(result.output, schema)
            return CheckResult(validator_id=self.validator_id, ok=True)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                validator_id=self.validator_id,
                ok=False,
                detail=str(exc),
            )
