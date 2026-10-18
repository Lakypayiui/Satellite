"""Control de escritorio por árbol de accesibilidad — contratos (F1).

Esto **no** es kernel: es la capa de tools + adaptadores opcionales. Aquí sólo
viven los contratos y modelos serializables; los backends reales (Windows/macOS/
Linux) llegan en fases posteriores y siempre son dependencias **opcionales**.

Regla de oro del subsistema: **todo lo leído de la UI es DATO NO CONFIABLE**. La
seguridad vive en código determinista (allowlist, validador, política,
aprobación), nunca en el prompt ni en lo que diga un título/etiqueta.

Sobre ``Snapshot.elements`` vs ``Snapshot.text``: el **texto** compacto que ve un
agente incluye sólo elementos visibles+habilitados+interactuables (§3 del
addendum). ``elements`` conserva además el resto con sus banderas para que el
validador determinista pueda distinguir «no existe» de «existe pero no es
interactuable» (§4).
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from satellite.core.errors import ContractError

# ``quality`` de un snapshot: útil o degradado (con motivo).
SNAPSHOT_QUALITIES: tuple[str, ...] = ("good", "poor")
# ``mode`` de serialización: completo o diff contra la observación anterior.
SNAPSHOT_MODES: tuple[str, ...] = ("full", "diff")


@dataclass(frozen=True)
class WindowInfo:
    """Ventana de escritorio observada."""

    id: str
    title: str = ""
    app: str = ""
    active: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ContractError("WindowInfo: id no puede estar vacío")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "app": self.app,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WindowInfo":
        return cls(
            id=d["id"],
            title=str(d.get("title", "")),
            app=str(d.get("app", "")),
            active=bool(d.get("active", False)),
        )


@dataclass(frozen=True)
class Element:
    """Nodo del árbol de accesibilidad.

    ``bounds`` no se serializa por defecto (queda en ``None``); sólo lo fija el
    *fallback* por coordenadas, apagado por defecto. ``sensitive`` marca campos
    de contraseña: su valor nunca se muestra ni se escribe.
    """

    ref: str
    role: str
    name: str = ""
    value: str = ""
    enabled: bool = True
    visible: bool = True
    interactable: bool = True
    sensitive: bool = False
    bounds: tuple[int, int, int, int] | None = None
    actions: tuple[str, ...] = ()
    # AutomationId de UIA cuando el backend lo conoce: base de la clasificación
    # determinista de acciones destructivas (nunca se confía en la etiqueta).
    automation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str) or not self.ref.strip():
            raise ContractError("Element: ref no puede estar vacío")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ContractError("Element: role no puede estar vacío")
        if self.bounds is not None:
            if len(self.bounds) != 4 or not all(isinstance(v, int) for v in self.bounds):
                raise ContractError("Element: bounds debe ser (x, y, w, h) de enteros")

    @property
    def is_operable(self) -> bool:
        """Visible, habilitado e interactuable: lo único que se le muestra al agente."""
        return bool(self.visible and self.enabled and self.interactable)

    def to_dict(self) -> dict:
        data = {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "enabled": self.enabled,
            "visible": self.visible,
            "interactable": self.interactable,
            "sensitive": self.sensitive,
            "actions": list(self.actions),
            "automation_id": self.automation_id,
        }
        # Sin bounds salvo que el fallback los exija (§3).
        if self.bounds is not None:
            data["bounds"] = list(self.bounds)
        return data

    @classmethod
    def from_dict(cls, d: dict) -> "Element":
        raw_bounds = d.get("bounds")
        bounds = tuple(int(v) for v in raw_bounds) if raw_bounds is not None else None
        return cls(
            ref=d["ref"],
            role=d["role"],
            name=str(d.get("name", "")),
            value=str(d.get("value", "")),
            enabled=bool(d.get("enabled", True)),
            visible=bool(d.get("visible", True)),
            interactable=bool(d.get("interactable", True)),
            sensitive=bool(d.get("sensitive", False)),
            bounds=bounds,
            actions=tuple(str(a) for a in d.get("actions", ())),
            automation_id=str(d.get("automation_id", "")),
        )


@dataclass(frozen=True)
class ObserveLimits:
    """Topes de observación: elementos, caracteres y modo (full|diff)."""

    max_elements: int = 200
    max_chars: int = 8000
    mode: str = "full"

    def __post_init__(self) -> None:
        if not isinstance(self.max_elements, int) or self.max_elements <= 0:
            raise ContractError("ObserveLimits: max_elements debe ser > 0")
        if not isinstance(self.max_chars, int) or self.max_chars <= 0:
            raise ContractError("ObserveLimits: max_chars debe ser > 0")
        if self.mode not in SNAPSHOT_MODES:
            raise ContractError(
                f"ObserveLimits: mode debe ser uno de {SNAPSHOT_MODES}, recibido {self.mode!r}"
            )

    def to_dict(self) -> dict:
        return {
            "max_elements": self.max_elements,
            "max_chars": self.max_chars,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ObserveLimits":
        return cls(
            max_elements=int(d.get("max_elements", 200)),
            max_chars=int(d.get("max_chars", 8000)),
            mode=str(d.get("mode", "full")),
        )


@dataclass(frozen=True)
class Snapshot:
    """Observación compacta de una ventana.

    ``text`` es lo que se envuelve como dato no confiable y se pasa al agente;
    ``elements`` es el árbol observado (con banderas) para el validador.
    """

    snapshot_id: str
    window: WindowInfo
    elements: tuple[Element, ...] = ()
    text: str = ""
    quality: str = "good"
    reason: str = ""
    mode: str = "full"
    previous_snapshot_id: str = ""
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ContractError("Snapshot: snapshot_id no puede estar vacío")
        if self.quality not in SNAPSHOT_QUALITIES:
            raise ContractError(
                f"Snapshot: quality debe ser uno de {SNAPSHOT_QUALITIES}, "
                f"recibido {self.quality!r}"
            )
        if self.mode not in SNAPSHOT_MODES:
            raise ContractError(
                f"Snapshot: mode debe ser uno de {SNAPSHOT_MODES}, recibido {self.mode!r}"
            )

    def find(self, ref: str) -> Element | None:
        """Elemento por referencia efímera (``None`` si no está en esta observación)."""
        for element in self.elements:
            if element.ref == ref:
                return element
        return None

    def operable_elements(self) -> tuple[Element, ...]:
        """Sólo visibles + habilitados + interactuables."""
        return tuple(e for e in self.elements if e.is_operable)

    def signature(self) -> str:
        """Hash estable del contenido operable: sirve para «¿cambió algo?»."""
        payload = "\n".join(
            "|".join(
                (
                    e.ref,
                    e.role,
                    e.name,
                    e.value,
                    str(int(e.enabled)),
                    str(int(e.visible)),
                    str(int(e.interactable)),
                    str(int(e.sensitive)),
                )
            )
            for e in self.operable_elements()
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "window": self.window.to_dict(),
            "elements": [e.to_dict() for e in self.elements],
            "text": self.text,
            "quality": self.quality,
            "reason": self.reason,
            "mode": self.mode,
            "previous_snapshot_id": self.previous_snapshot_id,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(
            snapshot_id=d["snapshot_id"],
            window=WindowInfo.from_dict(d["window"]),
            elements=tuple(Element.from_dict(x) for x in d.get("elements", ())),
            text=str(d.get("text", "")),
            quality=str(d.get("quality", "good")),
            reason=str(d.get("reason", "")),
            mode=str(d.get("mode", "full")),
            previous_snapshot_id=str(d.get("previous_snapshot_id", "")),
            truncated=bool(d.get("truncated", False)),
        )


@dataclass(frozen=True)
class Capabilities:
    """Qué puede hacer un backend, y por qué vía."""

    platform: str
    can_observe: bool = True
    can_act: bool = False
    actions: tuple[str, ...] = ()
    coordinate_fallback: bool = False
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.platform, str) or not self.platform.strip():
            raise ContractError("Capabilities: platform no puede estar vacío")

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "can_observe": self.can_observe,
            "can_act": self.can_act,
            "actions": list(self.actions),
            "coordinate_fallback": self.coordinate_fallback,
            "sources": list(self.sources),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Capabilities":
        return cls(
            platform=d["platform"],
            can_observe=bool(d.get("can_observe", True)),
            can_act=bool(d.get("can_act", False)),
            actions=tuple(str(a) for a in d.get("actions", ())),
            coordinate_fallback=bool(d.get("coordinate_fallback", False)),
            sources=tuple(str(s) for s in d.get("sources", ())),
        )


@dataclass(frozen=True)
class Action:
    """Intención de acción sobre el ÚLTIMO snapshot.

    Es dato no confiable: el validador determinista decide si se ejecuta o no.
    ``coordinates`` sólo se admite con el *fallback* encendido (apagado por
    defecto).
    """

    kind: str
    target: str = ""
    text: str = ""
    amount: int | None = None
    direction: str = ""
    coordinates: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ContractError("Action: kind no puede estar vacío")
        if self.coordinates is not None and (
            len(self.coordinates) != 2 or not all(isinstance(v, int) for v in self.coordinates)
        ):
            raise ContractError("Action: coordinates debe ser (x, y) de enteros")

    def to_dict(self) -> dict:
        data = {
            "action": self.kind,
            "target": self.target,
            "text": self.text,
            "amount": self.amount,
            "direction": self.direction,
        }
        if self.coordinates is not None:
            data["coordinates"] = list(self.coordinates)
        return data

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        raw_kind = d.get("action", d.get("kind", ""))
        raw_coords = d.get("coordinates")
        coordinates = tuple(int(v) for v in raw_coords) if raw_coords is not None else None
        raw_amount = d.get("amount")
        return cls(
            kind=str(raw_kind),
            target=str(d.get("target", "")),
            text=str(d.get("text", "")),
            amount=int(raw_amount) if raw_amount is not None else None,
            direction=str(d.get("direction", "")),
            coordinates=coordinates,
        )


@dataclass(frozen=True)
class ActResult:
    """Resultado de intentar actuar (puede ser un fallo claro sin ejecución)."""

    ok: bool = True
    action: str = ""
    ref: str = ""
    code: str = ""
    error: str = ""
    changed: bool = False
    before_snapshot_id: str = ""
    after_snapshot_id: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "action": self.action,
            "ref": self.ref,
            "code": self.code,
            "error": self.error,
            "changed": self.changed,
            "before_snapshot_id": self.before_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ActResult":
        return cls(
            ok=bool(d.get("ok", True)),
            action=str(d.get("action", "")),
            ref=str(d.get("ref", "")),
            code=str(d.get("code", "")),
            error=str(d.get("error", "")),
            changed=bool(d.get("changed", False)),
            before_snapshot_id=str(d.get("before_snapshot_id", "")),
            after_snapshot_id=str(d.get("after_snapshot_id", "")),
        )


class DesktopBackend(ABC):
    """Adaptador de plataforma (opcional). Nunca depende del kernel.

    Los métodos son ``async`` para encajar con el resto del framework (toda IO
    se declara ``async``); los backends reales los implementarán con su biblioteca
    opcional.
    """

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Qué puede hacer este backend y con qué acciones."""

    @abstractmethod
    async def list_windows(self) -> list[WindowInfo]:
        """Ventanas de nivel superior visibles."""

    @abstractmethod
    async def observe(
        self,
        window: WindowInfo | str,
        limits: ObserveLimits | None = None,
    ) -> Snapshot:
        """Observa una ventana y devuelve un snapshot compacto."""

    async def inspect(self, snapshot_id: str) -> Snapshot | None:
        """Snapshot vigente para política/confirmación (``None`` si obsoleto).

        No es abstracto: los backends que no guardan snapshots devuelven ``None``
        y la capa de política cae al lado seguro (confirmar/denegar).
        """
        return None

    @abstractmethod
    async def act(self, snapshot_id: str, action: Action) -> ActResult:
        """Ejecuta una acción ya validada contra el snapshot vigente."""
