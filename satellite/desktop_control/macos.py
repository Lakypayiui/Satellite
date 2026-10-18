"""Backend macOS Accessibility (AX) — opcional y de import perezoso (F6).

Dependencias opcionales (extra ``desktop-control-macos``):
- ``pyobjc-framework-ApplicationServices`` (AXUIElement*)
- ``pyobjc-framework-Quartz`` (CGWindowList*)

No se importan a nivel de módulo. Requiere que el proceso tenga permiso de
Accesibilidad concedido en Ajustes del Sistema; si no, se devuelve
``EnvironmentUnavailable`` (nunca se elevan privilegios).

Acciones nativas soportadas: ``click`` (AXPress/AXConfirm/AXOpen), ``focus``
(AXFocused) y ``set_value`` (AXValue si es settable). ``type``/``key``/``scroll``
no tienen patrón AX fiable ⇒ ``unsupported_action`` explícito (sin CGEvent ni
coordenadas). El resto de la seguridad (snapshot vigente, allowlist, redacción
sensible, re-observación) la aporta ``AdapterDesktopBackend``.
"""

from __future__ import annotations

import sys
from typing import Any

from satellite.desktop_control.adapter_backend import AdapterDesktopBackend, DesktopAdapter
from satellite.desktop_control.config import normalize_app
from satellite.desktop_control.errors import (
    EnvironmentUnavailable,
    MissingDependency,
    UnsupportedPlatform,
)

PLATFORM = "darwin"
EXTRA = "desktop-control-macos"
MACOS_EXTRA = "desktop-control-macos"
SUPPORTED_ACTIONS: tuple[str, ...] = ("click", "focus", "set_value")
MAX_WINDOWS = 40

# Rolenames de AX → nombres canónicos (los usa la clasificación destructiva).
ROLE_MAP: dict[str, str] = {
    "AXButton": "Button",
    "AXCheckBox": "CheckBox",
    "AXRadioButton": "RadioButton",
    "AXPopUpButton": "ComboBox",
    "AXTextField": "Edit",
    "AXTextArea": "Edit",
    "AXSecureTextField": "Edit",
    "AXMenuItem": "MenuItem",
    "AXTab": "TabItem",
    "AXRow": "ListItem",
    "AXScrollBar": "ScrollBar",
    "AXSlider": "Slider",
    "AXLink": "Hyperlink",
    "AXComboBox": "ComboBox",
}
INTERACTABLE_ROLES: frozenset[str] = frozenset(ROLE_MAP.values())
SECURE_ROLES: frozenset[str] = frozenset({"AXSecureTextField"})
PRESS_ACTIONS: tuple[str, ...] = ("AXPress", "AXConfirm", "AXOpen")


def _import_ax() -> tuple[Any, Any]:
    """Importa pyobjc (AX + Quartz) o falla diciendo el extra exacto."""
    try:
        import ApplicationServices as AX  # noqa: PLC0415 — import perezoso
        import Quartz  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise MissingDependency(
            "control de escritorio en macOS: faltan "
            "pyobjc-framework-ApplicationServices y pyobjc-framework-Quartz. "
            f"Instala el extra: pip install -e .[{EXTRA}]",
            extra=EXTRA,
        ) from exc
    return AX, Quartz


def _load_ax() -> tuple[Any, Any]:
    """Valida la plataforma y carga pyobjc (perezoso)."""
    if not sys.platform.startswith("darwin"):
        raise UnsupportedPlatform(
            "control de escritorio AX sólo está disponible en macOS "
            f"(plataforma actual: {sys.platform})"
        )
    return _import_ax()


def ax_available() -> bool:
    """¿Se puede importar pyobjc AX/Quartz? (no importa a nivel de módulo)."""
    try:
        _import_ax()
    except Exception:
        return False
    return True


class _AxAdapter:
    """Adaptador real: AXUIElement + CGWindowList → diccionarios del contrato."""

    def __init__(self, AX: Any, Quartz: Any) -> None:
        self._AX = AX
        self._Quartz = Quartz
        if not getattr(AX, "AXIsProcessTrusted", lambda: True)():
            raise EnvironmentUnavailable(
                "macOS: concede permiso de Accesibilidad a este proceso "
                "(Ajustes del Sistema → Privacidad y seguridad → Accesibilidad)"
            )
        self._windows: dict[str, dict[str, Any]] = {}

    # --- lectura ---------------------------------------------------------

    def list_windows(self, *, max_windows: int) -> list[dict[str, Any]]:
        option = self._Quartz.kCGWindowListOptionOnScreenOnly | self._Quartz.kCGWindowListExcludeDesktopElements
        try:
            infos = self._Quartz.CGWindowListCopyWindowInfo(option, 0) or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for info in infos:
            if len(out) >= max_windows:
                break
            try:
                if int(info.get(self._Quartz.kCGWindowLayer, 0)) != 0:
                    continue
                if float(info.get(self._Quartz.kCGWindowAlpha, 1.0)) <= 0:
                    continue
                window_id = int(info.get(self._Quartz.kCGWindowNumber, 0))
                if not window_id:
                    continue
            except Exception:
                continue
            title = str(info.get(self._Quartz.kCGWindowName, "") or "")
            app = str(info.get(self._Quartz.kCGWindowOwnerName, "") or "")
            try:
                pid = int(info.get(self._Quartz.kCGWindowOwnerPID, 0) or 0)
            except Exception:
                pid = 0
            key = str(window_id)
            self._windows[key] = {"pid": pid, "title": title, "app": app}
            out.append(
                {
                    "id": key,
                    "title": title,
                    "app": normalize_app(app),
                    # La lista llega de delante a atrás: la primera es la activa.
                    "active": len(out) == 0,
                }
            )
        return out

    def window_elements(self, window_id: str, *, max_elements: int) -> list[dict[str, Any]]:
        window = self._window_element(window_id)
        if window is None:
            return []
        return [node for _element, node in self._walk(window, max_elements)]

    # --- acción ----------------------------------------------------------

    def act(self, window_id: str, node_key: str, action: dict[str, Any]) -> dict[str, Any]:
        kind = str(action.get("action", ""))
        text = str(action.get("text", ""))
        window = self._window_element(window_id)
        if window is None:
            return {"ok": False, "code": "target_missing", "error": "ventana no disponible"}
        element = self._find(window, node_key)
        if element is None:
            return {
                "ok": False,
                "code": "target_missing",
                "error": "el control ya no está en el árbol AX",
            }
        if kind == "click":
            for name in PRESS_ACTIONS:
                try:
                    if int(self._AX.AXUIElementPerformAction(element, name)) == 0:
                        return {"ok": True, "code": "", "error": ""}
                except Exception:
                    continue
            return {
                "ok": False,
                "code": "unsupported_action",
                "error": "el control no expone AXPress/AXConfirm/AXOpen",
            }
        if kind == "focus":
            try:
                if int(self._AX.AXUIElementSetAttributeValue(element, "AXFocused", True)) == 0:
                    return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}
            return {"ok": False, "code": "unsupported_action", "error": "no se pudo enfocar"}
        if kind == "set_value":
            try:
                _err, settable = self._AX.AXUIElementIsAttributeSettable(
                    element, "AXValue", None
                )
                if settable and int(
                    self._AX.AXUIElementSetAttributeValue(element, "AXValue", text)
                ) == 0:
                    return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}
            return {
                "ok": False,
                "code": "unsupported_action",
                "error": "AXValue no es settable en este control",
            }
        return {
            "ok": False,
            "code": "unsupported_action",
            "error": f"macOS AX no ofrece un patrón nativo seguro para {kind!r}",
        }

    # --- internos --------------------------------------------------------

    def _window_element(self, window_id: str) -> Any | None:
        meta = self._windows.get(str(window_id))
        if meta is None or not meta.get("pid"):
            return None
        try:
            app = self._AX.AXUIElementCreateApplication(int(meta["pid"]))
            windows = self._attr(app, "AXWindows") or []
        except Exception:
            return None
        for candidate in windows:
            if str(self._attr(candidate, "AXTitle") or "") == meta.get("title", ""):
                return candidate
        return windows[0] if windows else None

    def _walk(self, window: Any, limit: int) -> list[tuple[Any, dict[str, Any]]]:
        """DFS pre-order determinista; la clave incluye el índice de recorrido."""
        out: list[tuple[Any, dict[str, Any]]] = []
        stack: list[Any] = [window]
        index = 0
        while stack and len(out) < limit:
            element = stack.pop()
            node = self._node(element, index)
            if node is not None:
                out.append((element, node))
            index += 1
            children = self._attr(element, "AXChildren") or []
            try:
                stack.extend(reversed(list(children)))
            except Exception:
                continue
        return out

    def _find(self, window: Any, node_key: str) -> Any | None:
        for element, node in self._walk(window, 10_000):
            if node.get("key") == node_key:
                return element
        return None

    def _node(self, element: Any, index: int) -> dict[str, Any] | None:
        try:
            raw_role = str(self._attr(element, "AXRole") or "")
            subrole = str(self._attr(element, "AXSubrole") or "")
            title = str(self._attr(element, "AXTitle") or self._attr(element, "AXDescription") or "")
            identifier = str(self._attr(element, "AXIdentifier") or "")
            sensitive = raw_role in SECURE_ROLES or subrole in SECURE_ROLES
            enabled_value = self._attr(element, "AXEnabled")
            enabled = True if enabled_value is None else bool(enabled_value)
            hidden = bool(self._attr(element, "AXHidden") or False)
            actions = tuple(str(a) for a in (self._attr(element, "AXActionNames") or ()))
            role = ROLE_MAP.get(raw_role, raw_role.replace("AX", "") or "Unknown")
            value = "" if sensitive else self._string(self._attr(element, "AXValue"))
            return {
                "key": f"{raw_role}:{identifier or title}:{index}",
                "role": role,
                "name": title,
                "value": value,
                "enabled": enabled,
                "visible": not hidden,
                "interactable": bool(not hidden and enabled and role in INTERACTABLE_ROLES),
                "sensitive": sensitive,
                "actions": list(actions),
                "automation_id": identifier,
            }
        except Exception:
            return None

    def _attr(self, element: Any, name: str) -> Any:
        try:
            _err, value = self._AX.AXUIElementCopyAttributeValue(element, name, None)
        except Exception:
            return None
        return value

    @staticmethod
    def _string(value: Any) -> str:
        if value is None:
            return ""
        try:
            return str(value)
        except Exception:
            return ""


class MacosAxBackend(AdapterDesktopBackend):
    """Backend macOS AX (click/focus/set_value; el resto degrada a error claro)."""

    platform = PLATFORM
    sources = ("pyobjc.ApplicationServices", "pyobjc.Quartz")
    can_act = True
    supported_actions = SUPPORTED_ACTIONS
    max_windows = MAX_WINDOWS

    def _make_adapter(self) -> DesktopAdapter:
        AX, Quartz = _load_ax()
        return _AxAdapter(AX, Quartz)
