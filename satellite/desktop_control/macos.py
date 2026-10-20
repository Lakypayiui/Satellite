"""Backend macOS Accessibility (AX) — opcional y de import perezoso (F6).

Dependencias opcionales (extra ``desktop-control-macos``):
- ``pyobjc-framework-ApplicationServices`` (AXUIElement*)
- ``pyobjc-framework-Quartz`` (CGWindowList*)

No se importan a nivel de módulo. Requiere que el proceso tenga permiso de
Accesibilidad concedido en Ajustes del Sistema; si no, se devuelve
``EnvironmentUnavailable`` (nunca se elevan privilegios).

Acciones nativas soportadas: ``click`` (AXPress/AXConfirm/AXOpen), ``focus``
(AXFocused), ``set_value`` (AXValue si es settable) y ``scroll`` (AXScrollBar
AXValue o acciones AXScroll*ByPage sobre el área de scroll). ``type``/``key`` no
tienen patrón AX fiable ⇒ ``unsupported_action`` explícito (sin CGEvent ni
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
SUPPORTED_ACTIONS: tuple[str, ...] = ("click", "focus", "set_value", "scroll")
SCROLL_PAGE_ACTIONS: dict[str, str] = {
    "down": "AXScrollDownByPage",
    "up": "AXScrollUpByPage",
    "left": "AXScrollLeftByPage",
    "right": "AXScrollRightByPage",
}
SCROLL_STEP = 0.1
MAX_SCROLL_STEPS = 50
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


def ax_trusted() -> bool | None:
    """¿Tiene el proceso permiso de Accesibilidad? ``None`` si no se puede saber."""
    try:
        AX, _Quartz = _import_ax()
        return bool(AX.AXIsProcessTrusted())
    except Exception:
        return None


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
        if kind == "scroll" and not node_key:
            return self._scroll(window, action)
        element = self._find(window, node_key)
        if element is None:
            return {
                "ok": False,
                "code": "target_missing",
                "error": "el control ya no está en el árbol AX",
            }
        if kind == "scroll":
            return self._scroll(element, action)
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

    # --- scroll ----------------------------------------------------------

    def _scroll(self, base: Any, action: dict[str, Any]) -> dict[str, Any]:
        """Scroll nativo: acción de página AX o AXValue de la barra de scroll."""
        direction = str(action.get("direction") or "down").strip().lower()
        if direction not in SCROLL_PAGE_ACTIONS:
            direction = "down"
        try:
            amount = int(action.get("amount") or 1)
        except (TypeError, ValueError):
            amount = 1
        amount = max(1, min(amount, MAX_SCROLL_STEPS))

        # 1) Acciones nativas de página (Safari/WebKit y áreas de scroll AX).
        if self._scroll_by_page(base, direction, amount):
            return {"ok": True, "code": "", "error": ""}

        # 2) Barra de scroll: AXValue (0.0–1.0) es settable.
        bar = self._scroll_bar(base, direction)
        if bar is not None:
            try:
                current = self._attr(bar, "AXValue")
                value = float(current) if current is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            delta = SCROLL_STEP * amount
            if direction in ("up", "left"):
                delta = -delta
            new_value = min(1.0, max(0.0, value + delta))
            try:
                if (
                    int(self._AX.AXUIElementSetAttributeValue(bar, "AXValue", new_value))
                    == 0
                ):
                    return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}

        return {
            "ok": False,
            "code": "unsupported_action",
            "error": "no hay área de scroll AX desplazable en este control",
        }

    def _scroll_by_page(self, base: Any, direction: str, amount: int) -> bool:
        """Repite la acción de página en el elemento y sus ancestros AX."""
        name = SCROLL_PAGE_ACTIONS[direction]
        node = base
        for _ in range(6):
            if node is None:
                break
            moved = False
            for _step in range(amount):
                try:
                    if int(self._AX.AXUIElementPerformAction(node, name)) != 0:
                        break
                    moved = True
                except Exception:
                    break
            if moved:
                return True
            node = self._attr(node, "AXParent")
        return False

    def _scroll_bar(self, base: Any, direction: str) -> Any | None:
        """Barra de scroll vertical/horizontal del área o de un ancestro."""
        if base is None:
            return None
        if str(self._attr(base, "AXRole") or "") == "AXScrollBar":
            return base
        attributes = (
            ("AXHorizontalScrollBar",)
            if direction in ("left", "right")
            else ("AXVerticalScrollBar",)
        )
        node = base
        for _ in range(6):
            if node is None:
                break
            for attribute in attributes:
                bar = self._attr(node, attribute)
                if bar is not None:
                    return bar
            node = self._attr(node, "AXParent")
        # Respaldo: cualquier AXScrollBar con la orientación pedida en el subárbol.
        wanted = "horizontal" if direction in ("left", "right") else "vertical"
        for element in self._iter_elements(base, limit=2000):
            if str(self._attr(element, "AXRole") or "") != "AXScrollBar":
                continue
            orientation = str(self._attr(element, "AXOrientation") or "").lower()
            if wanted in orientation:
                return element
        return None

    def _iter_elements(self, root: Any, *, limit: int):
        """Recorrido DFS acotado del subárbol AX (sin construir nodos)."""
        stack = [root]
        seen = 0
        while stack and seen < limit:
            element = stack.pop()
            seen += 1
            yield element
            try:
                children = list(self._attr(element, "AXChildren") or [])
            except Exception:
                children = []
            stack.extend(reversed(children))

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
        # AXWindowNumber es el mismo CGWindowID que devuelve CGWindowList y no
        # depende del título (que puede venir vacío sin permiso de grabación de
        # pantalla). Es la vía fiable para elegir la ventana correcta.
        for candidate in windows:
            try:
                number = self._attr(candidate, "AXWindowNumber")
                if number is not None and int(number) == int(window_id):
                    return candidate
            except (TypeError, ValueError):
                continue
        # Respaldo por título, y por último la primera ventana de la app.
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
            actions = self._actions(element)
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

    def _actions(self, element: Any) -> tuple[str, ...]:
        """Nombres de acción vía ``AXUIElementCopyActionNames`` (no es atributo)."""
        try:
            _err, names = self._AX.AXUIElementCopyActionNames(element, None)
        except Exception:
            return ()
        try:
            return tuple(str(name) for name in (names or ()))
        except Exception:
            return ()

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
