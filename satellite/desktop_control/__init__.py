"""Control de escritorio por árbol de accesibilidad (tools + adaptadores opcionales).

F1: contratos e interfaces, ``FakeBackend`` determinista, serialización compacta
de snapshots y validador determinista de acciones.
F2: backend Windows UIA de **sólo lectura** (dependencia opcional ``pywinauto``,
import perezoso) y selección por plataforma.

Nada de esto es kernel y todo lo leído de la UI es **dato no confiable**.
"""

from __future__ import annotations

from .actions import (
    ACTION_NOT_ALLOWED,
    ALLOWED_ACTIONS,
    ALLOWED_ACTIONS_SET,
    COORDINATES_DISABLED,
    DEFAULT_MAX_TEXT_CHARS,
    SENSITIVE_TARGET,
    SNAPSHOT_OBSOLETE,
    TARGET_MISSING,
    TARGET_NOT_INTERACTABLE,
    TEXT_TOO_LONG,
    ActionDecision,
    apply_action,
    validate_action,
)
from .base import (
    SNAPSHOT_MODES,
    SNAPSHOT_QUALITIES,
    ActResult,
    Action,
    Capabilities,
    DesktopBackend,
    Element,
    ObserveLimits,
    Snapshot,
    WindowInfo,
)
from .errors import (
    DesktopControlError,
    EnvironmentUnavailable,
    MissingDependency,
    UnsupportedPlatform,
)
from .adapter_backend import AdapterDesktopBackend, DesktopAdapter
from .linux import LINUX_EXTRA, LinuxAtspiBackend, atspi_available
from .macos import MACOS_EXTRA, MacosAxBackend, ax_available
from .history import OBSERVATIONS_FILE, ObservationHistory, ObservationLine
from .perception import (
    OCR_INTERFACE,
    VISION_INTERFACE,
    ConfiguredVisionFallback,
    OcrBox,
    OcrFallback,
    OcrResult,
    UnavailableOcrFallback,
    UnavailableVisionFallback,
    VisionFallback,
    degraded_options,
    vision_status,
)
from .factory import backend_for_platform
from .fake import FakeBackend, demo_elements, demo_windows
from .snapshot import build_snapshot, render_element
from .audit import audit_desktop_action, audit_log_for, ref_hash
from .config import (
    DEFAULT_DENIED_APPS,
    DesktopControlSettings,
    default_path as desktop_control_path,
    load_desktop_settings,
    normalize_app,
    write_desktop_settings,
)
from .destructive import classify_destructive, normalize_label
from .vision import (
    VisionSelection,
    resolve_vision_selection,
    save_selection as save_vision_selection,
    vision_fallback_for,
)
from .policy import (
    ACTION_BUDGET,
    APP_DENIED,
    APP_NOT_ALLOWED,
    APPROVAL_REQUIRED,
    CONFIRMATION_REQUIRED,
    DESKTOP_CONTROL_KIND,
    DISABLED,
    EMERGENCY_STOP,
    INVALID_APP,
    RATE_LIMITED,
    SCOPE_CONTROL,
    SCOPE_OBSERVE,
    SENSITIVE_TARGET,
    TIME_BUDGET,
    ActionBudget,
    DesktopControlPolicy,
    DesktopGrantStore,
    EmergencyStop,
    GateDecision,
)
from .windows import (
    MAX_WINDOWS,
    PYWINAUTO_RANGE,
    UiaAdapter,
    WindowsUiaBackend,
    pywinauto_available,
)

__all__ = [
    "ACTION_NOT_ALLOWED",
    "ALLOWED_ACTIONS",
    "ALLOWED_ACTIONS_SET",
    "COORDINATES_DISABLED",
    "DEFAULT_MAX_TEXT_CHARS",
    "MAX_WINDOWS",
    "PYWINAUTO_RANGE",
    "SENSITIVE_TARGET",
    "SNAPSHOT_MODES",
    "SNAPSHOT_OBSOLETE",
    "SNAPSHOT_QUALITIES",
    "TARGET_MISSING",
    "TARGET_NOT_INTERACTABLE",
    "TEXT_TOO_LONG",
    "ActResult",
    "Action",
    "ActionDecision",
    "Capabilities",
    "DesktopBackend",
    "DesktopControlError",
    "Element",
    "FakeBackend",
    "MissingDependency",
    "ObserveLimits",
    "Snapshot",
    "UiaAdapter",
    "UnsupportedPlatform",
    "WindowInfo",
    "WindowsUiaBackend",
    "apply_action",
    "backend_for_platform",
    "build_snapshot",
    "demo_elements",
    "demo_windows",
    "pywinauto_available",
    "render_element",
    "validate_action",
    # F4: política, grants, topes, emergencia, configuración local y auditoría.
    "ACTION_BUDGET",
    "APP_DENIED",
    "APP_NOT_ALLOWED",
    "APPROVAL_REQUIRED",
    "CONFIRMATION_REQUIRED",
    "DEFAULT_DENIED_APPS",
    "DESKTOP_CONTROL_KIND",
    "DISABLED",
    "EMERGENCY_STOP",
    "INVALID_APP",
    "RATE_LIMITED",
    "SCOPE_CONTROL",
    "SCOPE_OBSERVE",
    "SENSITIVE_TARGET",
    "TIME_BUDGET",
    "ActionBudget",
    "DesktopControlPolicy",
    "DesktopControlSettings",
    "DesktopGrantStore",
    "EmergencyStop",
    "GateDecision",
    "audit_desktop_action",
    "audit_log_for",
    "classify_destructive",
    "desktop_control_path",
    "load_desktop_settings",
    "normalize_app",
    "normalize_label",
    "ref_hash",
    # F6: backends macOS/Linux y base común de adaptadores.
    "AdapterDesktopBackend",
    "DesktopAdapter",
    "EnvironmentUnavailable",
    "LINUX_EXTRA",
    "LinuxAtspiBackend",
    "MACOS_EXTRA",
    "MacosAxBackend",
    "atspi_available",
    "ax_available",
    # F7: percepción degradada e historial de observaciones.
    "OBSERVATIONS_FILE",
    "OCR_INTERFACE",
    "VISION_INTERFACE",
    "ObservationHistory",
    "ObservationLine",
    "OcrBox",
    "OcrFallback",
    "OcrResult",
    "UnavailableOcrFallback",
    "UnavailableVisionFallback",
    "VisionFallback",
    "degraded_options",
    # Selección de visión y escritura local del host (sólo UI de Ajustes).
    "ConfiguredVisionFallback",
    "VisionSelection",
    "resolve_vision_selection",
    "save_vision_selection",
    "vision_fallback_for",
    "vision_status",
    "write_desktop_settings",
]
