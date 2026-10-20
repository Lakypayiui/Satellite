"""Clasificación determinista de acciones destructivas (F4).

No usa heurísticas sobre etiquetas: sólo igualdad **exacta** contra conjuntos
normalizados de AutomationId/nombre, y el tipo de control. Las etiquetas son dato
no confiable; aquí se usan como texto, nunca como regla difusa.

``classify_destructive`` devuelve:
- ``"destructive"``: coincidencia estricta o campo sensible.
- ``"safe"``: sólo si el tipo de control/acción permite afirmarlo (p. ej. escribir
  en un ``Edit`` normal, o ``focus``/``scroll``).
- ``"unknown"``: todo lo demás ⇒ la política exige confirmación humana.
"""

from __future__ import annotations

import unicodedata

# Acciones que no mutan el estado de la app.
NON_MUTATING_ACTIONS: frozenset[str] = frozenset({"focus", "scroll"})
TEXT_ACTIONS: frozenset[str] = frozenset({"type", "set_value"})
# Escribir en estos roles es entrada de texto normal (no borra/enviá/paga).
TEXT_SAFE_ROLES: frozenset[str] = frozenset({"Edit", "ComboBox", "Document"})
# Click sobre estos roles es un cambio local (no envía ni borra).
CLICK_SAFE_ROLES: frozenset[str] = frozenset(
    {"CheckBox", "RadioButton", "ListItem", "TabItem", "TreeItem", "Slider"}
)

# AutomationId normalizados considerados destructivos (igualdad exacta).
DESTRUCTIVE_IDS: frozenset[str] = frozenset(
    {
        "delete",
        "deletebutton",
        "remove",
        "removebutton",
        "erase",
        "send",
        "sendbutton",
        "submit",
        "submitbutton",
        "post",
        "publish",
        "pay",
        "payment",
        "purchase",
        "buy",
        "overwrite",
        "replace",
        "discard",
        "reset",
        "restoredefaults",
        "format",
        "wipe",
        "confirmdelete",
    }
)
# Nombres normalizados destructivos (igualdad exacta, no subcadena).
DESTRUCTIVE_NAMES: frozenset[str] = frozenset(
    {
        "delete",
        "remove",
        "erase",
        "borrar",
        "eliminar",
        "send",
        "submit",
        "post",
        "enviar",
        "publicar",
        "pay",
        "purchase",
        "buy",
        "pagar",
        "comprar",
        "overwrite",
        "sobrescribir",
        "replace",
        "reemplazar",
        "discard",
        "descartar",
        "reset",
        "restablecer",
        "erase all",
        "delete all",
        "format",
        "formatear",
        "wipe",
        "close without saving",
        "cerrar sin guardar",
        "discard changes",
        "descartar cambios",
    }
)


def normalize_label(value: object) -> str:
    """Minúsculas, sin acentos, espacios colapsados."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def classify_destructive(element: object, action: str = "") -> str:
    """``destructive`` | ``safe`` | ``unknown`` (determinista, sin heurísticas)."""
    kind = str(action or "").strip().lower()
    # ``scroll``/``focus`` no cambian el estado de la app: son seguras incluso si
    # el scroll va sin target (ventana completa).
    if kind in NON_MUTATING_ACTIONS:
        return "safe"
    if element is None:
        return "unknown"
    if bool(getattr(element, "sensitive", False)):
        return "destructive"

    automation = normalize_label(getattr(element, "automation_id", ""))
    name = normalize_label(getattr(element, "name", ""))
    role = str(getattr(element, "role", "") or "")

    if automation in DESTRUCTIVE_IDS or name in DESTRUCTIVE_NAMES:
        return "destructive"
    if kind in TEXT_ACTIONS:
        return "safe" if role in TEXT_SAFE_ROLES else "unknown"
    if kind == "click":
        return "safe" if role in CLICK_SAFE_ROLES else "unknown"
    # `key` y cualquier otra acción: no se puede afirmar que sea inocua.
    return "unknown"
