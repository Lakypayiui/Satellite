"""Backend Windows UIA (F3/F6): lectura + ``act`` con patrones **nativos**.

La lógica de seguridad (snapshots, obsolescencia, validación determinista,
redacción sensible, resolución ref→control, re-observación) vive en
``AdapterDesktopBackend`` y es común a Windows/macOS/Linux.

``pywinauto`` es una dependencia **opcional** (extra ``desktop-control``)
importada de forma perezosa; este módulo no la importa a nivel de módulo.

Limitaciones honestas de UIA (ver README):
- El árbol puede estar incompleto o desactualizado; si no hay elementos útiles se
  marca ``quality=poor`` en lugar de fingir una observación buena.
- Procesos con mayor integridad (elevados) pueden no ser legibles sin elevación;
  **nunca se elevan privilegios aquí**.
- Apps Electron/Chromium necesitan exponer su árbol de accesibilidad (muchas sólo
  lo hacen con la accesibilidad habilitada).
"""

from __future__ import annotations

import sys
from typing import Any

from .actions import ALLOWED_ACTIONS
from .adapter_backend import AdapterDesktopBackend, DesktopAdapter as UiaAdapter
from .config import normalize_app
from .errors import MissingDependency, UnsupportedPlatform

PLATFORM = "win32"
# Extra de pyproject que instala la dependencia opcional.
EXTRA = "desktop-control"
# Rango estable compatible: 0.6.9 es el último estable; la 1.0.0b1 es beta (no prerelease).
PYWINAUTO_RANGE = "pywinauto>=0.6.9,<1.0"
# Tope de ventanas a enumerar (nunca todo el escritorio sin límites).
MAX_WINDOWS = 40

# Tipos de control UIA considerados interactuables (allowlist conservadora).
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
        "TreeItem",
        "ScrollBar",
        "Slider",
        "Spinner",
        "SplitButton",
        "DataItem",
        "HeaderItem",
    }
)
# Acciones plausibles por rol (informativas; el validador decide).
ROLE_ACTIONS: dict[str, tuple[str, ...]] = {
    "Button": ("click",),
    "CheckBox": ("click",),
    "RadioButton": ("click",),
    "MenuItem": ("click",),
    "TabItem": ("click",),
    "Hyperlink": ("click",),
    "ListItem": ("click",),
    "Edit": ("set_value", "type", "focus"),
    "ComboBox": ("click", "set_value"),
    "ScrollBar": ("scroll",),
}


def _import_pywinauto() -> Any:
    """Importa pywinauto o falla con un error que dice el extra exacto."""
    try:
        import pywinauto  # noqa: PLC0415 — import perezoso a propósito
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise MissingDependency(
            "control de escritorio: falta pywinauto. "
            f"Instala el extra: pip install -e .[{EXTRA}]",
            extra=EXTRA,
        ) from exc
    return pywinauto


def _load_pywinauto() -> Any:
    """Valida la plataforma y carga pywinauto (perezoso)."""
    if not sys.platform.startswith("win"):
        raise UnsupportedPlatform(
            "control de escritorio UIA sólo está disponible en Windows "
            f"(plataforma actual: {sys.platform})"
        )
    return _import_pywinauto()


def pywinauto_available() -> bool:
    """¿Se puede importar pywinauto? (no importa a nivel de módulo el paquete)."""
    try:
        _import_pywinauto()
    except Exception:
        return False
    return True


class _NoPattern(Exception):
    """El control no expone un patrón nativo para la acción pedida."""


def _process_image(pid: int) -> str:
    """Ruta del ejecutable del proceso (best-effort, Windows, stdlib).

    Se usa para obtener el nombre de app que aplica la allowlist/denylist local.
    Si falla (proceso protegido/elevado), devuelve cadena vacía: la política
    denegará la app por no poder identificarla.
    """
    if not pid:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return ""
            return str(buffer.value or "")
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


class _PywinautoAdapter:
    """Adaptador real: traduce pywinauto/UIA a los diccionarios del contrato."""

    def __init__(self, module: Any) -> None:
        self._pywinauto = module

    def list_windows(self, *, max_windows: int) -> list[dict[str, Any]]:
        desktop = self._pywinauto.Desktop(backend="uia")
        active = self._active_handle(desktop)
        out: list[dict[str, Any]] = []
        for win in desktop.windows(visible_only=True, enabled_only=False):
            if len(out) >= max_windows:
                break
            try:
                handle = int(win.handle)
            except Exception:
                continue
            try:
                title = str(win.window_text() or "")
            except Exception:
                title = ""
            try:
                pid = int(getattr(win.element_info, "process_id", 0) or 0)
            except Exception:
                pid = 0
            out.append(
                {
                    "id": str(handle),
                    "title": title,
                    # Nombre de proceso para allowlist/denylist/grants (vacío si no
                    # se puede identificar ⇒ la política lo denegará).
                    "app": normalize_app(_process_image(pid)),
                    "active": handle == active,
                }
            )
        return out

    def window_elements(self, window_id: str, *, max_elements: int) -> list[dict[str, Any]]:
        desktop = self._pywinauto.Desktop(backend="uia")
        try:
            win = desktop.window(handle=int(window_id))
        except Exception:
            return []
        try:
            descendants = win.descendants()
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for item in descendants:
            if len(out) >= max_elements:
                break
            node = self._node(item)
            if node is not None:
                out.append(node)
        return out

    def act(self, window_id: str, node_key: str, action: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta una acción con patrones nativos; nunca usa coordenadas."""
        kind = str(action.get("action", ""))
        text = str(action.get("text", ""))
        desktop = self._pywinauto.Desktop(backend="uia")
        try:
            win = desktop.window(handle=int(window_id))
        except Exception as exc:
            return {"ok": False, "code": "target_missing", "error": f"ventana no disponible: {exc}"}

        # `key` es global (no necesita un control concreto).
        if kind == "key":
            try:
                win.type_keys(text, set_foreground=True)
                return {"ok": True, "code": "", "error": ""}
            except Exception as exc:
                return {"ok": False, "code": "act_failed", "error": str(exc)}

        item = self._find(win, node_key)
        if item is None:
            return {
                "ok": False,
                "code": "target_missing",
                "error": "el control ya no está en el árbol UIA",
            }
        try:
            self._native(item, kind, text, action)
            return {"ok": True, "code": "", "error": ""}
        except _NoPattern as exc:
            return {"ok": False, "code": "unsupported_action", "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "code": "act_failed", "error": str(exc)}

    def _find(self, win: Any, node_key: str) -> Any | None:
        try:
            descendants = win.descendants()
        except Exception:
            return None
        for item in descendants:
            try:
                if self._key(item.element_info) == node_key:
                    return item
            except Exception:
                continue
        return None

    def _native(self, item: Any, kind: str, text: str, action: dict[str, Any]) -> Any:
        """Patrón nativo por acción; si no hay, ``_NoPattern`` (sin coordenadas)."""
        if kind == "click":
            return self._first(item, ("invoke", "select", "toggle"))
        if kind == "focus":
            return self._first(item, ("set_focus",))
        if kind == "set_value":
            return self._first(item, ("set_value", "set_edit_text"), text)
        if kind == "type":
            try:
                item.set_focus()
            except Exception:
                pass
            return self._first(item, ("type_keys",), text)
        if kind == "scroll":
            direction = str(action.get("direction") or "down")
            amount = action.get("amount")
            amount = amount if isinstance(amount, int) else 1
            return self._first(item, ("scroll",), direction, amount)
        raise _NoPattern(f"acción sin patrón nativo: {kind}")

    @staticmethod
    def _first(item: Any, methods: tuple[str, ...], *args: Any) -> Any:
        """Primer patrón nativo que funcione; ninguno ⇒ ``_NoPattern``."""
        last: Exception | None = None
        for name in methods:
            method = getattr(item, name, None)
            if not callable(method):
                continue
            try:
                return method(*args)
            except Exception as exc:  # se prueba el siguiente patrón
                last = exc
        detail = f" ({last})" if last else ""
        raise _NoPattern(f"el control no admite {methods[0]}{detail}")

    def _node(self, item: Any) -> dict[str, Any] | None:
        try:
            info = item.element_info
        except Exception:
            return None
        try:
            visible = bool(getattr(info, "visible", True))
            enabled = bool(getattr(info, "enabled", True))
            # UIA expone IsPassword en campos de contraseña; si no, se asume False.
            sensitive = bool(getattr(info, "is_password", False))
            role = str(getattr(info, "control_type", "") or "")
            try:
                name = str(item.window_text() or "")
            except Exception:
                name = ""
            value = "" if sensitive else self._value(item)
            return {
                "key": self._key(info),
                "role": role or "Unknown",
                "name": name,
                "value": value,
                "enabled": enabled,
                "visible": visible,
                "interactable": bool(visible and enabled and role in INTERACTABLE_ROLES),
                "sensitive": sensitive,
                "actions": list(ROLE_ACTIONS.get(role, ())),
                "automation_id": str(getattr(info, "automation_id", "") or ""),
            }
        except Exception:
            return None

    @staticmethod
    def _value(item: Any) -> str:
        try:
            value = item.get_value()
        except Exception:
            return ""
        return str(value) if value is not None else ""

    @staticmethod
    def _key(info: Any) -> str:
        for attr in ("runtime_id", "automation_id"):
            try:
                value = getattr(info, attr, None)
            except Exception:
                continue
            if value:
                return f"{attr}:{value}"
        return ""

    @staticmethod
    def _active_handle(desktop: Any) -> int | None:
        # La detección de ventana activa es best-effort: si la API no responde,
        # ninguna se marca activa (degradación explícita, nunca se asume).
        for attempt in (
            lambda: desktop.active_window().handle,
            lambda: desktop.window(active_only=True).handle,
        ):
            try:
                handle = attempt()
            except Exception:
                continue
            if handle:
                try:
                    return int(handle)
                except Exception:
                    continue
        return None


class WindowsUiaBackend(AdapterDesktopBackend):
    """Backend Windows UIA: lectura + ``act`` con patrones nativos."""

    platform = PLATFORM
    sources = ("pywinauto.uia",)
    can_act = True
    supported_actions = tuple(ALLOWED_ACTIONS)
    max_windows = MAX_WINDOWS

    def _make_adapter(self) -> UiaAdapter:
        return _PywinautoAdapter(_load_pywinauto())
