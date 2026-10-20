"""Backend Linux AT-SPI — opcional y de import perezoso (F6).

Dependencias opcionales (extra ``desktop-control-linux`` + paquetes del sistema):
- PyGObject (``gi``) con el typelib ``Atspi 2.0`` (``gir1.2-atspi-2.0``);
- un bus de accesibilidad AT-SPI activo en el escritorio.

No se importa a nivel de módulo. Si falta el extra/typelib se devuelve
``MissingDependency``; si no hay bus/permiso, ``EnvironmentUnavailable``. Nunca se
elevan privilegios.

Acciones nativas: ``click`` (Action.do_action), ``focus`` (Component.grab_focus),
``set_value`` (EditableText.set_text_contents) y ``scroll`` (Value de la barra de
scroll o acciones AT-SPI de página). ``type``/``key`` no tienen patrón AT-SPI
fiable ⇒ ``unsupported_action`` explícito (sin coordenadas). El resto de la
seguridad la aporta ``AdapterDesktopBackend``.
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

PLATFORM = "linux"
EXTRA = "desktop-control-linux"
LINUX_EXTRA = "desktop-control-linux"
SUPPORTED_ACTIONS: tuple[str, ...] = ("click", "focus", "set_value", "scroll")
MAX_WINDOWS = 40

# Rolenames de AT-SPI → nombres canónicos (los usa la clasificación destructiva).
ROLE_MAP: dict[str, str] = {
    "push button": "Button",
    "toggle button": "Button",
    "check box": "CheckBox",
    "radio button": "RadioButton",
    "entry": "Edit",
    "password text": "Edit",
    "text": "Text",
    "document text": "Document",
    "combo box": "ComboBox",
    "menu item": "MenuItem",
    "page tab": "TabItem",
    "list item": "ListItem",
    "scroll bar": "ScrollBar",
    "slider": "Slider",
    "link": "Hyperlink",
}
INTERACTABLE_ROLES: frozenset[str] = frozenset(
    {
        "Button",
        "CheckBox",
        "RadioButton",
        "ComboBox",
        "Edit",
        "Hyperlink",
        "ListItem",
        "MenuItem",
        "TabItem",
        "ScrollBar",
        "Slider",
        "TreeItem",
    }
)
SECURE_ROLES: frozenset[str] = frozenset({"password text"})
CLICK_ACTIONS: frozenset[str] = frozenset(
    {"click", "press", "activate", "open", "toggle", "do default"}
)
WINDOW_ROLES: frozenset[str] = frozenset({"frame", "dialog", "window", "alert"})


def _import_atspi() -> Any:
    """Importa PyGObject/Atspi o falla diciendo el extra y el paquete del sistema."""
    try:
        import gi  # noqa: PLC0415 — import perezoso

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # noqa: PLC0415
    except (ImportError, ValueError) as exc:  # pragma: no cover - depende del entorno
        raise MissingDependency(
            "control de escritorio en Linux: falta PyGObject con el typelib Atspi 2.0. "
            f"Instala el extra: pip install -e .[{EXTRA}] y el paquete del sistema "
            "'gir1.2-atspi-2.0'; además la accesibilidad del escritorio debe estar activa.",
            extra=EXTRA,
        ) from exc
    return Atspi


def _load_atspi() -> Any:
    """Valida la plataforma y carga AT-SPI (perezoso)."""
    if not sys.platform.startswith("linux"):
        raise UnsupportedPlatform(
            "control de escritorio AT-SPI sólo está disponible en Linux "
            f"(plataforma actual: {sys.platform})"
        )
    return _import_atspi()


def atspi_available() -> bool:
    """¿Se puede importar PyGObject/Atspi? (no importa a nivel de módulo)."""
    try:
        _import_atspi()
    except Exception:
        return False
    return True


class _AtspiAdapter:
    """Adaptador real: AT-SPI (PyGObject) → diccionarios del contrato."""

    def __init__(self, Atspi: Any) -> None:
        self._Atspi = Atspi
        try:
            Atspi.init()
        except Exception as exc:  # pragma: no cover - depende del entorno
            raise EnvironmentUnavailable(
                f"AT-SPI no pudo inicializarse: {exc}. Activa la accesibilidad "
                "del escritorio y comprueba el bus AT-SPI."
            ) from exc
        try:
            desktop = Atspi.get_desktop(0)
        except Exception as exc:  # pragma: no cover
            raise EnvironmentUnavailable(f"AT-SPI no responde: {exc}") from exc
        if desktop is None:  # pragma: no cover - depende del entorno
            raise EnvironmentUnavailable(
                "no hay bus AT-SPI: activa la accesibilidad del escritorio "
                "(p. ej. gnome-shell --enable-accessibility o el panel de accesibilidad)."
            )
        self._desktop = desktop
        self._windows: dict[str, dict[str, Any]] = {}

    # --- lectura ---------------------------------------------------------

    def list_windows(self, *, max_windows: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            app_count = int(self._desktop.get_child_count())
        except Exception:
            return []
        for app_index in range(app_count):
            try:
                app = self._desktop.get_child_at_index(app_index)
            except Exception:
                continue
            if app is None:
                continue
            app_name = self._name(app)
            try:
                child_count = int(app.get_child_count())
            except Exception:
                continue
            for child_index in range(child_count):
                try:
                    window = app.get_child_at_index(child_index)
                except Exception:
                    continue
                if window is None or self._role(window) not in WINDOW_ROLES:
                    continue
                window_id = f"atspi-{app_index}-{child_index}"
                title = self._name(window)
                self._windows[window_id] = {
                    "app_index": app_index,
                    "child_index": child_index,
                    "title": title,
                }
                out.append(
                    {
                        "id": window_id,
                        "title": title,
                        "app": normalize_app(app_name),
                        "active": self._state(window, "ACTIVE")
                        or self._state(window, "FOCUSED"),
                    }
                )
                if len(out) >= max_windows:
                    return out
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
                "error": "el control ya no está en el árbol AT-SPI",
            }
        if kind == "scroll":
            return self._scroll(element, action)
        if kind == "click":
            action_iface = self._query(element, "query_action")
            if action_iface is None:
                return {
                    "ok": False,
                    "code": "unsupported_action",
                    "error": "el control no expone la interfaz Action",
                }
            try:
                for index in range(int(action_iface.get_n_actions())):
                    name = str(action_iface.get_action_name(index) or "").strip().lower()
                    if name in CLICK_ACTIONS:
                        action_iface.do_action(index)
                        return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}
            return {
                "ok": False,
                "code": "unsupported_action",
                "error": "ninguna acción AT-SPI coincide con click/press/activate/open",
            }
        if kind == "focus":
            component = self._query(element, "query_component")
            if component is None:
                return {
                    "ok": False,
                    "code": "unsupported_action",
                    "error": "el control no expone la interfaz Component",
                }
            try:
                if component.grab_focus():
                    return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}
            return {"ok": False, "code": "unsupported_action", "error": "grab_focus falló"}
        if kind == "set_value":
            editable = self._query(element, "query_editable_text")
            if editable is None:
                return {
                    "ok": False,
                    "code": "unsupported_action",
                    "error": "el control no expone EditableText",
                }
            try:
                if editable.set_text_contents(text):
                    return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}
            return {"ok": False, "code": "unsupported_action", "error": "set_text_contents falló"}
        return {
            "ok": False,
            "code": "unsupported_action",
            "error": f"Linux AT-SPI no ofrece un patrón nativo seguro para {kind!r}",
        }

    # --- scroll ----------------------------------------------------------

    def _scroll(self, base: Any, action: dict[str, Any]) -> dict[str, Any]:
        """Scroll nativo: Value de la barra de scroll o acción AT-SPI de página."""
        direction = str(action.get("direction") or "down").strip().lower()
        if direction not in ("up", "down", "left", "right"):
            direction = "down"
        try:
            amount = int(action.get("amount") or 1)
        except (TypeError, ValueError):
            amount = 1
        amount = max(1, min(amount, 50))

        # 1) Barra de scroll con interfaz Value (la mayoría de toolkits).
        bar = self._scroll_bar(base)
        value = self._query(bar, "query_value") if bar is not None else None
        if value is not None:
            try:
                current = float(value.get_current_value())
                minimum = float(value.get_minimum_value())
                maximum = float(value.get_maximum_value())
                try:
                    step = float(value.get_minimum_increment())
                except Exception:
                    step = 0.0
                if step <= 0:
                    step = max((maximum - minimum) * 0.1, 1.0)
                delta = step * amount
                if direction in ("up", "left"):
                    delta = -delta
                target = min(maximum, max(minimum, current + delta))
                value.set_current_value(target)
                return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}

        # 2) Acciones AT-SPI con nombre de scroll/página.
        names = {
            "down": ("scroll down", "page down", "down"),
            "up": ("scroll up", "page up", "up"),
            "left": ("scroll left", "page left", "left"),
            "right": ("scroll right", "page right", "right"),
        }[direction]
        node = base
        for _ in range(6):
            if node is None:
                break
            action_iface = self._query(node, "query_action")
            if action_iface is not None:
                try:
                    for index in range(int(action_iface.get_n_actions())):
                        name = str(action_iface.get_action_name(index) or "").lower()
                        if any(wanted in name for wanted in names):
                            for _step in range(amount):
                                action_iface.do_action(index)
                            return {"ok": True, "code": "", "error": ""}
                except Exception as exc:
                    return {"ok": False, "code": "act_failed", "error": str(exc)}
            node = self._query(node, "get_parent")

        return {
            "ok": False,
            "code": "unsupported_action",
            "error": "no hay barra de scroll ni acción AT-SPI desplazable",
        }

    def _scroll_bar(self, base: Any) -> Any | None:
        """Primera barra de scroll (preferentemente vertical) del subárbol."""
        if base is None:
            return None
        if self._role(base) == "scroll bar":
            return base
        fallback = None
        for element in self._iter_elements(base, limit=2000):
            if self._role(element) != "scroll bar":
                continue
            name = self._name(element).lower()
            if "vertical" in name or "vert" in name:
                return element
            if fallback is None:
                fallback = element
        return fallback

    def _iter_elements(self, root: Any, *, limit: int):
        """Recorrido DFS acotado del subárbol AT-SPI."""
        stack = [root]
        seen = 0
        while stack and seen < limit:
            element = stack.pop()
            seen += 1
            yield element
            try:
                children = [
                    element.get_child_at_index(index)
                    for index in range(int(element.get_child_count()))
                ]
            except Exception:
                children = []
            stack.extend(reversed([child for child in children if child is not None]))

    # --- internos --------------------------------------------------------

    def _window_element(self, window_id: str) -> Any | None:
        meta = self._windows.get(str(window_id))
        if meta is None:
            return None
        try:
            app = self._desktop.get_child_at_index(meta["app_index"])
            return app.get_child_at_index(meta["child_index"]) if app is not None else None
        except Exception:
            return None

    def _walk(self, window: Any, limit: int) -> list[tuple[Any, dict[str, Any]]]:
        out: list[tuple[Any, dict[str, Any]]] = []
        stack: list[Any] = [window]
        index = 0
        while stack and len(out) < limit:
            element = stack.pop()
            node = self._node(element, index)
            if node is not None:
                out.append((element, node))
            index += 1
            try:
                children = [
                    element.get_child_at_index(i)
                    for i in range(int(element.get_child_count()))
                ]
            except Exception:
                children = []
            stack.extend(reversed([c for c in children if c is not None]))
        return out

    def _find(self, window: Any, node_key: str) -> Any | None:
        for element, node in self._walk(window, 10_000):
            if node.get("key") == node_key:
                return element
        return None

    def _node(self, element: Any, index: int) -> dict[str, Any] | None:
        try:
            raw_role = self._role(element)
            role = ROLE_MAP.get(raw_role, raw_role.title() or "Unknown")
            name = self._name(element)
            sensitive = raw_role in SECURE_ROLES or self._state(element, "PROTECTED")
            enabled = self._state(element, "ENABLED")
            visible = self._state(element, "VISIBLE") or self._state(element, "SHOWING")
            automation_id = ""
            get_id = getattr(element, "get_accessible_id", None)
            if callable(get_id):
                try:
                    automation_id = str(get_id() or "")
                except Exception:
                    automation_id = ""
            return {
                "key": f"{raw_role}:{name}:{index}",
                "role": role,
                "name": name,
                "value": "" if sensitive else self._value(element),
                "enabled": enabled,
                "visible": visible,
                "interactable": bool(visible and enabled and role in INTERACTABLE_ROLES),
                "sensitive": sensitive,
                "actions": list(self._actions(element)),
                "automation_id": automation_id,
            }
        except Exception:
            return None

    def _role(self, element: Any) -> str:
        try:
            return str(element.get_role_name() or "")
        except Exception:
            return ""

    def _name(self, element: Any) -> str:
        try:
            return str(element.get_name() or "")
        except Exception:
            return ""

    def _state(self, element: Any, name: str) -> bool:
        state_type = getattr(self._Atspi.StateType, name, None)
        if state_type is None:
            return False
        try:
            return bool(element.get_state_set().contains(state_type))
        except Exception:
            return False

    def _query(self, element: Any, method: str) -> Any | None:
        query = getattr(element, method, None)
        if not callable(query):
            return None
        try:
            return query()
        except Exception:
            return None

    def _actions(self, element: Any) -> tuple[str, ...]:
        action = self._query(element, "query_action")
        if action is None:
            return ()
        try:
            return tuple(
                str(action.get_action_name(i) or "")
                for i in range(int(action.get_n_actions()))
            )
        except Exception:
            return ()

    def _value(self, element: Any) -> str:
        text = self._query(element, "query_text")
        if text is not None:
            try:
                count = int(text.get_character_count())
                return str(text.get_text(0, count) or "")
            except Exception:
                pass
        value = self._query(element, "query_value")
        if value is not None:
            try:
                return str(value.get_current_value())
            except Exception:
                return ""
        return ""


class LinuxAtspiBackend(AdapterDesktopBackend):
    """Backend Linux AT-SPI (click/focus/set_value; el resto error claro)."""

    platform = PLATFORM
    sources = ("PyGObject.Atspi",)
    can_act = True
    supported_actions = SUPPORTED_ACTIONS
    max_windows = MAX_WINDOWS

    def _make_adapter(self) -> DesktopAdapter:
        return _AtspiAdapter(_load_atspi())
