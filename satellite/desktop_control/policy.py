"""Política de control de escritorio (F4): grants, topes y emergencia.

Todo determinista y **fuera del kernel**. La allowlist/denylist sale de la config
local del host; el modelo no puede cambiarla. Los grants son en memoria, por
sesión+app+scope y revocables. La confirmación destructiva es por acción.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .actions import TARGET_NOT_INTERACTABLE, TEXT_WRITE_ACTIONS
from .config import DesktopControlSettings, normalize_app
from .destructive import classify_destructive

DESKTOP_CONTROL_KIND = "desktop_control"
SCOPE_OBSERVE = "observe"
SCOPE_CONTROL = "control"
# Clave de grant para listar todas las ventanas. No es un nombre de app y nunca
# debe pasar por ``app_decision``.
ALL_WINDOWS = "*"

# Códigos estables para tests/telemetría.
DISABLED = "desktop_disabled"
INVALID_APP = "invalid_app"
APP_DENIED = "app_denied"
APP_NOT_ALLOWED = "app_not_allowed"
EMERGENCY_STOP = "emergency_stop"
ACTION_BUDGET = "action_budget"
TIME_BUDGET = "time_budget"
RATE_LIMITED = "rate_limited"
APPROVAL_REQUIRED = "approval_required"
CONFIRMATION_REQUIRED = "confirmation_required"
SENSITIVE_TARGET = "sensitive_target"
TARGET_MISSING = "target_missing"


@dataclass(frozen=True)
class GateDecision:
    """Resultado determinista de un gate (no ejecuta nada)."""

    allowed: bool
    code: str = ""
    reason: str = ""
    # Hay que pedir aprobación humana para conceder el par sesión+app.
    needs_approval: bool = False
    # Hay que confirmar esta acción concreta (aunque exista grant).
    needs_confirmation: bool = False

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "needs_approval": self.needs_approval,
            "needs_confirmation": self.needs_confirmation,
        }


def _allow() -> GateDecision:
    return GateDecision(True)


def _deny(code: str, reason: str) -> GateDecision:
    return GateDecision(False, code, reason)


class DesktopGrantStore:
    """Grants en memoria: ``(session_id, app, scope)``; revocables."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str, str], None] = {}

    def grant(self, session_id: str, app: object, scope: str) -> None:
        """Concede (idempotente) el par sesión+app para ese scope."""
        key = (str(session_id), normalize_app(app), str(scope))
        if not key[0] or not key[1] or not key[2]:
            return
        self._grants[key] = None

    def revoke(
        self,
        session_id: str,
        app: object | None = None,
        scope: str | None = None,
    ) -> int:
        """Revoca grants (filtro opcional); devuelve cuántos quitó."""
        sid = str(session_id)
        name = normalize_app(app) if app is not None else None
        wanted = str(scope) if scope is not None else None
        keys = [
            key
            for key in self._grants
            if key[0] == sid
            and (name is None or key[1] == name)
            and (wanted is None or key[2] == wanted)
        ]
        for key in keys:
            self._grants.pop(key, None)
        return len(keys)

    def is_granted(self, session_id: str, app: object, scope: str) -> bool:
        """True sólo para el par exacto sesión+app+scope (sin comodines implícitos)."""
        return (str(session_id), normalize_app(app), str(scope)) in self._grants

    def clear(self) -> None:
        self._grants.clear()

    def entries(self) -> list[dict]:
        return [
            {"session_id": sid, "app": app, "scope": scope}
            for (sid, app, scope) in sorted(self._grants)
        ]


class EmergencyStop:
    """Parada de emergencia local: se comprueba antes de cada acción."""

    def __init__(self) -> None:
        self._tripped = False
        self._reason = ""

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str:
        return self._reason

    def engage(self, reason: str = "") -> None:
        self._tripped = True
        self._reason = str(reason or "parada de emergencia")

    def clear(self) -> None:
        self._tripped = False
        self._reason = ""


@dataclass
class ActionBudget:
    """Topes por ejecución (acciones y tiempo) + ritmo por minuto."""

    settings: DesktopControlSettings
    clock: Callable[[], float] = time.monotonic
    actions: int = 0
    started: float = 0.0
    recent: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Arranca con el reloj inyectado (los tests usan un reloj falso).
        if not self.started:
            self.started = self.clock()

    def check(self) -> GateDecision:
        if self.actions >= self.settings.max_actions_per_run:
            return _deny(ACTION_BUDGET, "tope de acciones por ejecución alcanzado")
        if self.clock() - self.started > self.settings.max_seconds_per_run:
            return _deny(TIME_BUDGET, "tope de tiempo por ejecución alcanzado")
        cutoff = self.clock() - 60.0
        self.recent = [stamp for stamp in self.recent if stamp >= cutoff]
        if len(self.recent) >= self.settings.rate_per_minute:
            return _deny(RATE_LIMITED, "ritmo de acciones por minuto excedido")
        return _allow()

    def note(self) -> None:
        """Registra una acción efectiva (sólo tras ejecutarse con éxito)."""
        self.actions += 1
        self.recent.append(self.clock())

    def reset_run(self) -> None:
        """Reinicia los topes de ejecución (el runtime lo hace por tarea)."""
        self.actions = 0
        self.started = self.clock()
        self.recent.clear()


class DesktopControlPolicy:
    """Allowlist/denylist + grants + topes + emergencia + confirmación."""

    def __init__(
        self,
        settings: DesktopControlSettings | None = None,
        *,
        grants: DesktopGrantStore | None = None,
        emergency: EmergencyStop | None = None,
        budget: ActionBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
        allow_auto_approval: bool = False,
    ) -> None:
        self.settings = settings or DesktopControlSettings()
        self.grants = grants or DesktopGrantStore()
        self.emergency = emergency or EmergencyStop()
        self.budget = budget or ActionBudget(self.settings, clock=clock)
        self._run_id = ""
        # Sólo el modo ``unrestricted`` (decisión explícita del humano en Ajustes)
        # acepta el puerto automático. En ask/auto sigue mandando una persona.
        self.allow_auto_approval = bool(allow_auto_approval)

    def begin_run(self, run_id: str) -> None:
        """Reinicia los topes al empezar una ejecución nueva (una tarea).

        Sin esto el presupuesto de tiempo arrancaba al construir la política: tras
        ``max_seconds_per_run`` segundos desde el arranque, *cualquier* acción
        posterior fallaba con ``time_budget`` aunque la tarea acabara de empezar.
        """
        value = str(run_id or "")
        if not value or value == self._run_id:
            return
        self._run_id = value
        self.budget.reset_run()

    # --- allowlist / denylist ---

    def app_decision(self, app: object) -> GateDecision:
        """Decide si una app **concreta** está permitida por la config local.

        ``*`` no es una app: el listado global tiene su propio gate
        (``check_list_windows``) y aquí nunca se resuelve como allow implícito.
        """
        if not self.settings.enabled:
            return _deny(DISABLED, "control de escritorio deshabilitado en la config local")
        name = normalize_app(app)
        if not name:
            return _deny(INVALID_APP, "app vacía: no se puede aplicar la política")
        if name == "*":
            return _deny(INVALID_APP, "«*» no identifica una app concreta")
        if name in self.settings.denied_apps:
            return _deny(APP_DENIED, f"app vetada por la config local: {name}")
        if name not in self.settings.allowed_processes:
            return _deny(APP_NOT_ALLOWED, f"app fuera de la allowlist local: {name}")
        return _allow()

    def _pre(self) -> GateDecision:
        if self.emergency.tripped:
            return _deny(EMERGENCY_STOP, "parada de emergencia activa")
        return _allow()

    # --- observación ---

    def check_list_windows(self, session_id: str) -> GateDecision:
        """Gate de listar **todas** las ventanas: config + emergencia + grant.

        No hay app concreta que validar contra la allowlist; por eso este caso
        tiene un gate propio y no se representa como ``app_decision("*")``. La
        tool sigue excluyendo de la respuesta las apps de la denylist local.
        """
        if not self.settings.enabled:
            return _deny(DISABLED, "control de escritorio deshabilitado en la config local")
        pre = self._pre()
        if not pre.allowed:
            return pre
        if not self.grants.is_granted(session_id, ALL_WINDOWS, SCOPE_OBSERVE):
            return GateDecision(
                False,
                APPROVAL_REQUIRED,
                "falta aprobación humana para listar las ventanas de esta sesión",
                needs_approval=True,
            )
        return _allow()

    def check_screen(
        self,
        session_id: str,
        app: object = "*",
        scope: str = SCOPE_OBSERVE,
    ) -> GateDecision:
        """Gate de listar/observar: config + emergencia + grant sesión+app."""
        gate = self.app_decision(app)
        if not gate.allowed:
            return gate
        pre = self._pre()
        if not pre.allowed:
            return pre
        if not self.grants.is_granted(session_id, app, scope):
            return GateDecision(
                False,
                APPROVAL_REQUIRED,
                "falta aprobación humana para esta sesión y app",
                needs_approval=True,
            )
        return _allow()

    # --- acciones ---

    def check_action(
        self,
        action: str,
        element: object,
        app: object,
        session_id: str,
    ) -> GateDecision:
        """Gate de una acción concreta (no ejecuta nada)."""
        gate = self.app_decision(app)
        if not gate.allowed:
            return gate
        pre = self._pre()
        if not pre.allowed:
            return pre
        # ``scroll`` puede no llevar target: se desplaza el área principal.
        if element is None and str(action) != "scroll":
            return _deny(TARGET_MISSING, "target ausente en el snapshot vigente")
        if element is not None and not bool(getattr(element, "is_operable", True)):
            return _deny(
                TARGET_NOT_INTERACTABLE,
                "target no visible/habilitado/interactuable",
            )
        if (
            element is not None
            and str(action) in TEXT_WRITE_ACTIONS
            and bool(getattr(element, "sensitive", False))
        ):
            return _deny(SENSITIVE_TARGET, "campo sensible: nunca se escribe")
        budget = self.budget.check()
        if not budget.allowed:
            return budget
        if not self.grants.is_granted(session_id, app, SCOPE_CONTROL):
            return GateDecision(
                False,
                APPROVAL_REQUIRED,
                "falta aprobación humana para controlar esta sesión y app",
                needs_approval=True,
            )
        classification = classify_destructive(element, str(action))
        if self.settings.confirm_destructive and classification != "safe":
            return GateDecision(
                False,
                CONFIRMATION_REQUIRED,
                f"acción {classification}: requiere confirmación humana",
                needs_confirmation=True,
            )
        return _allow()

    def note_action(self) -> None:
        """Cuenta una acción efectiva para los topes."""
        self.budget.note()

    def reset_run(self) -> None:
        self.budget.reset_run()

    # --- grants ---

    def grant(self, session_id: str, app: object, scope: str) -> None:
        self.grants.grant(session_id, app, scope)

    def revoke(
        self,
        session_id: str,
        app: object | None = None,
        scope: str | None = None,
    ) -> int:
        return self.grants.revoke(session_id, app, scope)
