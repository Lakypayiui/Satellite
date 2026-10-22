"""Runner de skills: ejecuta el script por subproceso con JSON stdin/stdout."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from satellite.core.errors import ToolError
from satellite.sandbox.base import SandboxRunner, SandboxSpec
from satellite.skills.cache import SkillResultCache
from satellite.skills.models import SkillManifest
from satellite.skills.registry import SkillRegistry


@dataclass(frozen=True)
class SkillRunResult:
    """Resultado de ejecutar una skill."""

    skill_id: str
    success: bool
    output: Any = None
    error: str | None = None
    exit_code: int = 0
    duration_ms: int = 0
    cached: bool = False


class SkillRunner:
    """Ejecuta skills como subprocesos aislados (nunca importa su código).

    Contrato: JSON por stdin, JSON por stdout, exit code estándar.

    Con ``cache`` (``SkillResultCache``) memoiza el resultado de las skills
    **deterministas**: mismos manifest, script e input ⇒ no se vuelve a ejecutar.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        sandbox: SandboxRunner,
        *,
        default_timeout_s: int = 30,
        cache: "SkillResultCache | None" = None,
    ) -> None:
        self.registry = registry
        self.sandbox = sandbox
        self.default_timeout_s = default_timeout_s
        self.cache = cache

    def build_argv(self, manifest: SkillManifest) -> list[str]:
        """Construye el argv del intérprete para el script de la skill."""
        script = str(self.registry.root / manifest.id / manifest.entrypoint)
        if manifest.language == "python":
            return [sys.executable, script]
        if manifest.language == "bash":
            return ["bash", script]
        raise ToolError(f"lenguaje de skill no soportado: '{manifest.language}'")

    async def run(
        self,
        skill_id: str,
        payload: dict | None = None,
        *,
        project_root: Path,
        timeout_s: int | None = None,
        allow_network: bool = False,
        task_id: str = "",
        cancel=None,
    ) -> SkillRunResult:
        """Ejecuta la skill y devuelve su salida (con stats actualizadas).

        Emite ``SKILL_EXECUTED``/``SKILL_FAILED`` **sólo cuando la skill se ejecuta de
        verdad** (un acierto de caché también es un uso real). Estar en el catálogo, o
        haber sido considerada, no emite nada: el panel no puede confundirlos.

        ``cancel`` va al sandbox, que es quien sabe terminar el script que arrancó.
        """
        manifest = self.registry.get(skill_id)
        skill_dir = self.registry.root / manifest.id
        cache = self.cache
        if cache is not None and cache.eligible(
            manifest, allow_network=allow_network, skill_dir=skill_dir
        ):
            cache_key = cache.key_for(
                manifest,
                payload,
                project_root=project_root,
                skill_dir=skill_dir,
            )
        else:
            # ``None`` marca "no usar caché" y permite a mypy estrechar el tipo.
            cache = None
            cache_key = ""
        if cache is not None:
            hit = cache.get(cache_key)
            if hit is not None:
                # Un acierto es un uso real: cuenta para las métricas con coste ~0.
                self.registry.record_use(skill_id, True, 0)
                self._emit(
                    "executed", skill_id, task_id, cached=True, duration_ms=0
                )
                return SkillRunResult(
                    skill_id=skill_id, success=True, output=hit, cached=True
                )

        argv = self.build_argv(manifest)
        spec = SandboxSpec(
            project_root=Path(project_root),
            level=self.sandbox.level,
            workdir=Path(project_root),
            allow_network=allow_network,
            timeout_s=timeout_s or self.default_timeout_s,
            env={},
            stdin=json.dumps(payload or {}),
            cancel=cancel,
        )

        started = time.monotonic()
        raw = await self.sandbox.run(argv, spec)
        duration_ms = int((time.monotonic() - started) * 1000)

        if raw.cancelled:
            # Cancelar no es fallar: se cuenta el uso y se dice, pero no se emite
            # SKILL_FAILED (sería pintar una cancelación como un error de la skill).
            self.registry.record_use(skill_id, False, duration_ms)
            return SkillRunResult(
                skill_id=skill_id,
                success=False,
                error=raw.error or "cancelado",
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )

        if raw.timed_out or raw.error is not None:
            self.registry.record_use(skill_id, False, duration_ms)
            self._emit(
                "failed",
                skill_id,
                task_id,
                error=raw.error or "fallo del sandbox",
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )
            return SkillRunResult(
                skill_id=skill_id,
                success=False,
                error=raw.error or "fallo del sandbox",
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )

        if raw.exit_code != 0:
            self.registry.record_use(skill_id, False, duration_ms)
            self._emit(
                "failed",
                skill_id,
                task_id,
                error=(raw.stderr.strip() or f"exit code {raw.exit_code}"),
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )
            return SkillRunResult(
                skill_id=skill_id,
                success=False,
                error=(raw.stderr.strip() or f"exit code {raw.exit_code}"),
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )

        parsed, parse_error = self._parse_output(raw.stdout)
        if parse_error is not None:
            self.registry.record_use(skill_id, False, duration_ms)
            self._emit(
                "failed",
                skill_id,
                task_id,
                error=parse_error,
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )
            return SkillRunResult(
                skill_id=skill_id,
                success=False,
                error=parse_error,
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )

        schema_error = self._validate_output(manifest, parsed)
        if schema_error is not None:
            self.registry.record_use(skill_id, False, duration_ms)
            self._emit(
                "failed",
                skill_id,
                task_id,
                error=schema_error,
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )
            return SkillRunResult(
                skill_id=skill_id,
                success=False,
                error=schema_error,
                exit_code=raw.exit_code,
                duration_ms=duration_ms,
            )

        self.registry.record_use(skill_id, True, duration_ms)
        if cache is not None:
            # Sólo se memoizan éxitos: un fallo no se guarda nunca.
            cache.put(cache_key, parsed, skill_id=skill_id)
        self._emit(
            "executed", skill_id, task_id, cached=False, duration_ms=duration_ms
        )
        return SkillRunResult(
            skill_id=skill_id,
            success=True,
            output=parsed,
            exit_code=raw.exit_code,
            duration_ms=duration_ms,
        )

    def _emit(self, kind: str, skill_id: str, task_id: str, **payload: Any) -> None:
        """Emite el evento real de la ejecución; sin sink (o sin permiso) no pasa nada."""
        from satellite.core.events import EventType, ExecutionEvent
        from satellite.skills.registry import SYSTEM_TASK_ID

        sink = getattr(self.registry, "sink", None)
        if sink is None:
            return
        event_type = (
            EventType.SKILL_EXECUTED if kind == "executed" else EventType.SKILL_FAILED
        )
        sink.emit(
            ExecutionEvent(
                event_type=event_type,
                task_id=task_id or SYSTEM_TASK_ID,
                payload={"skill_id": skill_id, **payload},
            )
        )

    @staticmethod
    def _parse_output(stdout: str) -> tuple[Any, str | None]:
        text = stdout.strip()
        if not text:
            return {}, None
        try:
            return json.loads(text), None
        except json.JSONDecodeError as exc:
            return None, f"salida no es JSON válido: {exc}"

    @staticmethod
    def _validate_output(manifest: SkillManifest, output: Any) -> str | None:
        schema = manifest.outputs
        if not isinstance(schema, dict) or not schema.get("type"):
            return None
        try:
            import jsonschema

            jsonschema.validate(output, schema)
        except Exception as exc:  # noqa: BLE001
            return f"salida no cumple 'outputs': {exc}"
        return None
