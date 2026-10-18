"""Tool puente request_context: pide contexto adicional al ContextEngine."""

from __future__ import annotations

from pathlib import Path

from satellite.approval.base import ApprovalContext
from satellite.context.paths import canonical, is_within
from satellite.core.context_models import ContextRef, ContextRefKind
from satellite.core.proposal import ApprovalStatus
from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext


def _resolver_root(engine: object, tool_ctx: ToolContext) -> Path:
    """Raíz canónica del engine (la misma que usa el resolver para confinar)."""
    resolver = getattr(engine, "resolver", None)
    source = (
        getattr(resolver, "project_root", None)
        or getattr(engine, "project_root", None)
        or tool_ctx.project_root
    )
    return canonical(source) or Path(source)


class RequestContextTool(Tool):
    """Pide contexto adicional del proyecto (archivos o patrones) al engine."""

    @property
    def id(self) -> str:
        return "request_context"

    @property
    def description(self) -> str:
        return (
            "Pide contexto adicional del proyecto (archivos, directorios, "
            "patrones, símbolos o resumen) cuando necesitas más información."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("context",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["path", "dir", "pattern", "symbol", "summary"],
                            },
                            "value": {"type": "string"},
                        },
                        "required": ["kind", "value"],
                    },
                    "description": "Referencias de contexto a resolver.",
                }
            },
            "required": ["refs"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        engine = getattr(tool_ctx, "engine", None)
        if engine is None:
            return ToolResult(
                success=False,
                error="context engine no disponible",
            )
        raw_refs = arguments.get("refs", [])
        try:
            refs = [
                ContextRef(kind=ContextRefKind(r["kind"]), value=r["value"])
                for r in raw_refs
            ]
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                success=False,
                error=f"refs inválidos: {exc}",
            )

        # El cierre real vive en el resolver (deny-by-default sobre la ruta
        # canónica). Aquí sólo se pide la aprobación puntual por cada ruta de
        # fuera y se reenvía al engine como `allowed_paths` de ESTA petición: la
        # autorización no se persiste ni se recuerda entre solicitudes.
        root = _resolver_root(engine, tool_ctx)
        approved: list[Path] = []
        for ref in refs:
            if ref.kind not in (ContextRefKind.PATH, ContextRefKind.DIR):
                continue
            raw = Path(ref.value).expanduser()
            candidate = raw if raw.is_absolute() else root / raw
            resolved = canonical(candidate)
            # Interna válida (o irresoluble): no se pregunta nada.
            if resolved is None or is_within(resolved, root):
                continue
            approval = getattr(tool_ctx, "approval", None)
            if approval is None:
                return ToolResult(
                    success=False,
                    error=f"permiso requerido para leer fuera del proyecto: {resolved}",
                )
            summary = f"request_context solicita leer fuera del proyecto: {resolved}"
            decision = await approval.request(
                {"kind": "context_path", "path": str(resolved), "summary": summary},
                ApprovalContext(
                    kind="path",
                    task_id=tool_ctx.task_id,
                    summary=summary,
                    details={
                        "tool": self.id,
                        "path": str(resolved),
                        "project_root": str(root),
                        "requested": ref.value,
                        "consequence": "lectura de contexto",
                    },
                ),
            )
            if decision is None or decision.status is not ApprovalStatus.APPROVED:
                return ToolResult(
                    success=False,
                    error=f"lectura fuera del proyecto denegada: {resolved}",
                )
            approved.append(resolved)

        # Sin rutas externas aprobadas se usa la firma de siempre, para no exigir
        # `allowed_paths` a implementaciones/fakes de ContextEngine que no lo tengan.
        if approved:
            bundle = await engine.resolve(refs, allowed_paths=tuple(approved))
        else:
            bundle = await engine.resolve(refs)
        sections = []
        for section in bundle.sections:
            sections.append(
                {
                    "kind": section.ref.kind.value,
                    "value": section.ref.value,
                    "content": section.content,
                }
            )
        return ToolResult(success=True, output={"sections": sections})
