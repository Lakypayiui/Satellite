"""Guard de escritura: nada escribe en el kernel ni en la auditoría.

Control determinista (no depende del LLM): sólo se permite escribir bajo las
raíces de datos. Ni ``unrestricted`` ni el sandbox N2 lo saltan.
"""

from __future__ import annotations

from pathlib import Path

from satellite.core.errors import ToolError
from satellite.kernel.paths import DataPaths


class KernelWriteError(ToolError):
    """Intento de escritura fuera de las raíces de datos permitidas."""


def package_root() -> Path:
    """Raíz del paquete ``satellite/`` (el kernel)."""
    return Path(__file__).resolve().parents[1]


def writable_roots(paths: DataPaths) -> tuple[Path, ...]:
    """Raíces resueltas donde se permite escribir (datos globales + proyecto)."""
    return tuple(root.resolve() for root in paths.writable_roots())


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def is_write_allowed(
    path: Path | str,
    paths: DataPaths,
    *,
    kernel_root: Path | None = None,
) -> bool:
    """True si ``path`` está dentro de una raíz de datos y fuera del kernel/audit."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False

    kernel = (kernel_root or package_root()).resolve()
    audit = paths.audit_root.resolve()

    if _is_within(resolved, kernel):
        return False
    if _is_within(resolved, audit):
        return False
    return any(_is_within(resolved, root) for root in writable_roots(paths))


def assert_write_allowed(
    path: Path | str,
    paths: DataPaths,
    *,
    kernel_root: Path | None = None,
) -> None:
    """Lanza ``KernelWriteError`` si la escritura no está permitida."""
    if not is_write_allowed(path, paths, kernel_root=kernel_root):
        raise KernelWriteError(
            f"escritura denegada (fuera del directorio de datos o dentro del "
            f"kernel/auditoría): {path}"
        )
