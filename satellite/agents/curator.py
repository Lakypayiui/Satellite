"""SkillCurator: propone y aplica mantenimiento del catálogo de skills.

No crea skills de rutina: sólo deduplica, fusiona, generaliza, edita, retaguea,
deprecia o borra. Cada operación se valida de forma determinista (ids existentes,
no reservados) y pasa por el puerto de aprobación antes de aplicarse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from satellite.agents.prompting import build_meta_messages
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.schemas import CURATOR_OUTPUT_SCHEMA
from satellite.approval.base import ApprovalContext, ApprovalPort
from satellite.core.errors import ToolError
from satellite.core.events import EventType, ExecutionEvent
from satellite.core.messages import CallContext, LLMRequest
from satellite.kernel.manifest import is_reserved
from satellite.providers.base import LLMProvider
from satellite.providers.parsing import extract_json_object
from satellite.providers.prefix import fingerprint
from satellite.retrieval.sanitize import sanitize
from satellite.skills.models import SkillManifest, SkillStatus
from satellite.skills.registry import SkillRegistry

ALLOWED_OPS: tuple[str, ...] = (
    "merge",
    "edit",
    "deprecate",
    "delete",
    "retag",
    "flag_suspicious",
)


@dataclass(frozen=True)
class CuratorOperation:
    """Una operación propuesta por el curador."""

    op: str
    targets: tuple[str, ...]
    proposal: dict = field(default_factory=dict)
    reason: str = ""
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "targets": list(self.targets),
            "proposal": dict(self.proposal),
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class CuratorOutcome:
    """Resultado de una pasada del curador."""

    kind: str  # proposed | applied | empty | error
    operations: tuple[CuratorOperation, ...] = ()
    applied: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    reason: str = ""


class SkillCurator:
    """Mantiene el catálogo de skills bajo validación y aprobación."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: SkillRegistry,
        prompts: DefaultPromptRegistry,
        *,
        approval: ApprovalPort | None = None,
        sink=None,
        max_operations: int = 10,
        prompt_id: str = "skill_curator",
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.prompts = prompts
        self.approval = approval
        self.sink = sink
        self.max_operations = max_operations
        self.prompt_id = prompt_id

    async def propose(self, request: str = "", *, task_id: str = "") -> CuratorOutcome:
        """Pide al LLM un lote de operaciones y las parsea (sin aplicarlas)."""
        system_prompt = self.prompts.get_system(self.prompt_id)
        user_template = self.prompts.get_user(self.prompt_id)
        catalog_text = self._catalog_text()
        messages, stable = build_meta_messages(
            system_prompt=system_prompt,
            user_template=user_template,
            output_schema=CURATOR_OUTPUT_SCHEMA,
            catalog=catalog_text,
            task=request or "curaduría bajo demanda",
            context=self._stats_text(),
        )
        response = await self.provider.complete(
            LLMRequest(
                messages=messages,
                temperature=0.0,
                stable_user_count=stable,
                call=CallContext(role="curator", catalog_fp=fingerprint(catalog_text)),
            )
        )
        data = extract_json_object(response.content or "")
        if data is None:
            return CuratorOutcome(kind="error", reason="respuesta sin JSON válido")
        operations = self._parse(data)
        if not operations:
            return CuratorOutcome(kind="empty")
        return CuratorOutcome(kind="proposed", operations=operations)

    async def run(self, request: str = "", *, task_id: str = "", approval_port=None) -> CuratorOutcome:
        """Propone y aplica en una sola pasada."""
        proposed = await self.propose(request, task_id=task_id)
        if proposed.kind != "proposed":
            return proposed
        return await self.apply(
            proposed.operations,
            approval_port=approval_port if approval_port is not None else self.approval,
            task_id=task_id,
        )

    async def apply(
        self,
        operations: tuple[CuratorOperation, ...],
        *,
        approval_port: ApprovalPort | None = None,
        task_id: str = "",
    ) -> CuratorOutcome:
        """Valida y aplica operaciones (con aprobación por operación si hay puerto)."""
        applied: list[str] = []
        skipped: list[str] = []
        for op in operations:
            label = f"{op.op}:{','.join(op.targets)}"
            if not self._valid(op):
                skipped.append(label)
                continue
            port = approval_port if approval_port is not None else self.approval
            if port is not None:
                decision = await port.request(
                    op.to_dict(),
                    ApprovalContext(
                        kind="skill",
                        task_id=task_id,
                        summary=f"{op.op} {', '.join(op.targets)}",
                        details={"reason": op.reason, "proposal": op.proposal},
                    ),
                )
                if not decision.approved:
                    skipped.append(label)
                    continue
            try:
                self._mutate(op)
            except ToolError:
                skipped.append(label)
                continue
            applied.append(label)
        return CuratorOutcome(
            kind="applied",
            operations=operations,
            applied=tuple(applied),
            skipped=tuple(skipped),
        )

    # --- helpers ---

    def _parse(self, data: dict) -> tuple[CuratorOperation, ...]:
        raw = data.get("operations")
        if not isinstance(raw, list):
            return ()
        operations: list[CuratorOperation] = []
        for item in raw[: self.max_operations]:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op", ""))
            if op not in ALLOWED_OPS:
                continue
            targets = tuple(
                str(t) for t in (item.get("targets") or []) if str(t).strip()
            )
            if not targets:
                continue
            operations.append(
                CuratorOperation(
                    op=op,
                    targets=targets,
                    proposal=dict(item.get("proposal") or {}),
                    reason=str(item.get("reason", "")),
                    evidence=dict(item.get("evidence") or {}),
                )
            )
        return tuple(operations)

    def _valid(self, op: CuratorOperation) -> bool:
        if op.op not in ALLOWED_OPS:
            return False
        for target in op.targets:
            if is_reserved(target):
                return False
            try:
                self.registry.get(target)
            except ToolError:
                return False
        if op.op == "merge" and len(op.targets) < 2 and not op.proposal.get("keep_id"):
            return False
        return True

    def _mutate(self, op: CuratorOperation) -> None:
        if op.op == "flag_suspicious":
            self._emit_suspicious(op)
            return
        if op.op == "delete":
            self.registry.remove(op.targets[0])
            return
        if op.op == "deprecate":
            manifest = self.registry.get(op.targets[0])
            self.registry.replace_manifest(manifest.with_status(SkillStatus.DEPRECATED))
            return
        if op.op == "merge":
            keep = str(op.proposal.get("keep_id") or op.targets[0])
            for target in op.targets:
                if target != keep:
                    self.registry.remove(target)
            if op.proposal.get("description"):
                manifest = self.registry.get(keep)
                merged = SkillManifest.from_dict(
                    {**manifest.to_dict(), "description": str(op.proposal["description"])}
                )
                self.registry.replace_manifest(merged)
            return
        # edit | retag
        target = op.targets[0]
        data = self.registry.get(target).to_dict()
        if "description" in op.proposal:
            data["description"] = str(op.proposal["description"])
        if "tags" in op.proposal:
            data["tags"] = [str(t) for t in (op.proposal["tags"] or [])]
        self.registry.replace_manifest(SkillManifest.from_dict(data))

    def _catalog_text(self) -> str:
        """Catálogo **estable** (identidad de cada skill): forma el prefijo cacheable.

        Solo lo que define a la skill. Las estadísticas de uso van aparte porque
        cambian cada vez que se usa una skill: si entraran aquí, ni el prefijo
        sería cacheable ni ``catalog_fp`` detectaría una deriva real.
        """
        lines = [
            f"- id={manifest.id} | version={manifest.version} "
            f"| description={sanitize(manifest.description, max_chars=400)} "
            f"| tags={','.join(sorted(manifest.tags))} | status={manifest.status.value} "
            f"| lifecycle={manifest.lifecycle}"
            for manifest in self.registry.all()
        ]
        return "\n".join(lines) or "(catálogo vacío)"

    def _stats_text(self) -> str:
        """Estadísticas de uso (bloque **dinámico**, después del catálogo)."""
        lines = [
            f"- {manifest.id}: uses={manifest.stats.uses} "
            f"failures={manifest.stats.failures} "
            f"creation_tokens={manifest.stats.creation_tokens} "
            f"latency_ms={manifest.stats.total_latency_ms}"
            for manifest in self.registry.all()
        ]
        return "\n".join(lines) or "(sin estadísticas)"

    def _emit_suspicious(self, op: CuratorOperation) -> None:
        if self.sink is None:
            return
        self.sink.emit(
            ExecutionEvent(
                event_type=EventType.POLICY_VIOLATION,
                task_id="system",
                payload={
                    "kind": "flag_suspicious",
                    "targets": list(op.targets),
                    "reason": op.reason,
                },
            )
        )
