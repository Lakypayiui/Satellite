"""Validador determinista de acciones (§4).

Código puro: no consulta al LLM ni al prompt. Una acción es **dato no
confiable** y sólo se ejecuta si pasa esta validación contra el ÚLTIMO snapshot
(``snapshot_id`` vigente, target presente y operables, texto acotado). Si algo
falla, se devuelve un fallo claro **sin ejecutar nada**.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .base import ActResult, Action, DesktopBackend, Element, ObserveLimits, Snapshot

# Allowlist CERRADA de acciones (§2).
ALLOWED_ACTIONS: tuple[str, ...] = ("click", "type", "set_value", "key", "scroll", "focus")
ALLOWED_ACTIONS_SET: frozenset[str] = frozenset(ALLOWED_ACTIONS)
# Acciones que exigen un target presente e interactuable.
TARGET_ACTIONS: frozenset[str] = frozenset({"click", "type", "set_value", "scroll", "focus"})
# Acciones que escriben en un campo: nunca sobre uno sensible.
TEXT_WRITE_ACTIONS: frozenset[str] = frozenset({"type", "set_value"})
DEFAULT_MAX_TEXT_CHARS = 1000

# Códigos de fallo (deterministas y estables para tests/telemetría).
ACTION_NOT_ALLOWED = "action_not_allowed"
SNAPSHOT_OBSOLETE = "snapshot_obsolete"
TARGET_MISSING = "target_missing"
TARGET_NOT_INTERACTABLE = "target_not_interactable"
TEXT_TOO_LONG = "text_too_long"
COORDINATES_DISABLED = "coordinates_disabled"
SENSITIVE_TARGET = "sensitive_target"


@dataclass(frozen=True)
class ActionDecision:
    """Resultado de validar una acción (``ok`` + motivo determinista)."""

    ok: bool
    code: str = ""
    reason: str = ""
    element: Element | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "reason": self.reason,
            "element": self.element.to_dict() if self.element is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ActionDecision":
        raw = d.get("element")
        return cls(
            ok=bool(d.get("ok", False)),
            code=str(d.get("code", "")),
            reason=str(d.get("reason", "")),
            element=Element.from_dict(raw) if isinstance(raw, dict) else None,
        )


def _deny(code: str, reason: str) -> ActionDecision:
    return ActionDecision(ok=False, code=code, reason=reason)


def validate_action(
    action: Action,
    *,
    snapshot: Snapshot | None,
    snapshot_id: str = "",
    allowed_actions: frozenset[str] = ALLOWED_ACTIONS_SET,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    allow_coordinates: bool = False,
) -> ActionDecision:
    """Decide si una acción puede ejecutarse. **No ejecuta nada.**"""
    if action.kind not in allowed_actions:
        return _deny(ACTION_NOT_ALLOWED, f"acción no permitida: {action.kind!r}")

    if len(action.text) > max_text_chars:
        return _deny(
            TEXT_TOO_LONG,
            f"texto demasiado largo: {len(action.text)} > {max_text_chars}",
        )

    if action.coordinates is not None and not allow_coordinates:
        return _deny(
            COORDINATES_DISABLED,
            "coordenadas deshabilitadas (fallback apagado por defecto)",
        )

    if snapshot is None:
        return _deny(SNAPSHOT_OBSOLETE, "no hay snapshot vigente")
    if not snapshot_id or snapshot_id != snapshot.snapshot_id:
        return _deny(
            SNAPSHOT_OBSOLETE,
            f"snapshot obsoleto: {snapshot_id!r} != {snapshot.snapshot_id!r}",
        )

    element: Element | None = None
    if action.kind in TARGET_ACTIONS or action.target:
        if not action.target:
            return _deny(TARGET_MISSING, "la acción requiere un target")
        element = snapshot.find(action.target)
        if element is None:
            return _deny(TARGET_MISSING, f"target ausente en el snapshot: {action.target!r}")
        if not element.is_operable:
            return _deny(
                TARGET_NOT_INTERACTABLE,
                f"target no visible/habilitado/interactuable: {action.target!r}",
            )

    if element is not None and element.sensitive and action.kind in TEXT_WRITE_ACTIONS:
        return _deny(SENSITIVE_TARGET, "campo sensible: nunca se escribe")

    return ActionDecision(ok=True, element=element)


async def apply_action(
    backend: DesktopBackend,
    *,
    snapshot: Snapshot,
    action: Action,
    snapshot_id: str = "",
    limits: ObserveLimits | None = None,
    allowed_actions: frozenset[str] = ALLOWED_ACTIONS_SET,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    allow_coordinates: bool = False,
) -> ActResult:
    """Valida y, sólo si procede, actúa; después re-observa y dice si cambió.

    Si la validación falla **no se llama al backend** (no se ejecuta la acción).
    """
    sid = snapshot_id or snapshot.snapshot_id
    decision = validate_action(
        action,
        snapshot=snapshot,
        snapshot_id=sid,
        allowed_actions=allowed_actions,
        max_text_chars=max_text_chars,
        allow_coordinates=allow_coordinates,
    )
    if not decision.ok:
        return ActResult(
            ok=False,
            action=action.kind,
            ref=action.target,
            code=decision.code,
            error=decision.reason,
            before_snapshot_id=sid,
        )

    result = await backend.act(sid, action)
    if not result.ok:
        return result
    after = await backend.observe(snapshot.window, limits)
    return replace(
        result,
        changed=after.signature() != snapshot.signature(),
        before_snapshot_id=sid,
        after_snapshot_id=after.snapshot_id,
    )
