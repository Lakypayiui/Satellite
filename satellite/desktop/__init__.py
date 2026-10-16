"""Backend local para la app de escritorio: FastAPI sobre el runtime de siempre.

Si falta el extra ``desktop`` (FastAPI/uvicorn) se dice con claridad en vez de reventar
con un ``ImportError`` a mitad de un import.
"""

from __future__ import annotations

EXTRA_HINT = "pip install -e .[desktop]"


def missing_dependencies() -> list[str]:
    """Dependencias del extra ``desktop`` que no están instaladas."""
    faltan: list[str] = []
    for module in ("fastapi", "uvicorn"):
        try:
            __import__(module)
        except ImportError:
            faltan.append(module)
    return faltan


def require_dependencies() -> None:
    """Error explicable si falta el extra, nunca un import roto sin contexto."""
    faltan = missing_dependencies()
    if faltan:
        raise RuntimeError(
            "la app de escritorio necesita el extra 'desktop': falta "
            + ", ".join(faltan)
            + f" — instálalo con `{EXTRA_HINT}` y vuelve a intentarlo"
        )
