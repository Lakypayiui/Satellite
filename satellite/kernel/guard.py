"""Guard de escritura: zonas permitidas y zonas protegidas (siempre vetadas).

Modelo de zonas:

- **Permitido**: el espacio de trabajo (``workspace``), las raíces extra
  declaradas (``allowed_paths``) y los catálogos del runtime
  (``agents_root``/``skills_root``).
- **Protegido, y gana**: el paquete (``satellite/``, resuelto en runtime), el
  estado del framework (``<project_data>/state``), la auditoría y la
  configuración (``<project_data>/config``, que guarda la clave del proveedor).

El veto se evalúa sobre la ruta **ya resuelta**, así que un symlink del workspace
hacia una zona protegida cae solo. Control determinista (no depende del LLM).
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


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def protected_zone(
    path: Path | str,
    paths: DataPaths | None,
    *,
    kernel_root: Path | None = None,
) -> str:
    """Nombre de la zona protegida a la que pertenece ``path`` (``""`` si ninguna)."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return "kernel"
    kernel = (kernel_root or package_root()).resolve()
    if _is_within(resolved, kernel):
        return "kernel"
    if paths is None:
        return ""
    audit = Path(paths.audit_root).resolve()
    if _is_within(resolved, audit):
        return "audit"
    project_data = getattr(paths, "project_data", None)
    if project_data is not None:
        data = Path(project_data).resolve()
        for name, zone in (("state", "state"), ("config", "config")):
            if _is_within(resolved, (data / name).resolve()):
                return zone
    return ""


def writable_roots(
    paths: DataPaths,
    *,
    workspace: Path | str | None = None,
    allowed_paths: tuple = (),
) -> tuple[Path, ...]:
    """Raíces donde sí se puede escribir (resueltas, en orden estable)."""
    roots = [Path(paths.agents_root), Path(paths.skills_root)]
    if workspace is not None:
        roots.append(Path(workspace))
    roots.extend(Path(extra) for extra in allowed_paths or ())
    return tuple(root.resolve() for root in roots)


def is_write_allowed(
    path: Path | str,
    paths: DataPaths,
    *,
    kernel_root: Path | None = None,
    workspace: Path | str | None = None,
    allowed_paths: tuple = (),
) -> bool:
    """True si ``path`` cae en una zona permitida y en ninguna protegida."""
    if protected_zone(path, paths, kernel_root=kernel_root):
        return False
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return any(
        _is_within(resolved, root)
        for root in writable_roots(paths, workspace=workspace, allowed_paths=allowed_paths)
    )


def assert_write_allowed(
    path: Path | str,
    paths: DataPaths,
    *,
    kernel_root: Path | None = None,
    workspace: Path | str | None = None,
    allowed_paths: tuple = (),
) -> None:
    """Lanza ``KernelWriteError`` si la escritura no está permitida.

    El mensaje **nombra la zona** cuando está protegida, para que quien lo lea
    sepa por qué y no tenga que adivinar.
    """
    zone = protected_zone(path, paths, kernel_root=kernel_root)
    if zone:
        raise KernelWriteError(
            f"zona protegida ({zone}): no se puede escribir {path}"
        )
    if not is_write_allowed(
        path,
        paths,
        kernel_root=kernel_root,
        workspace=workspace,
        allowed_paths=allowed_paths,
    ):
        raise KernelWriteError(
            f"escritura denegada (fuera del espacio de trabajo y de los catálogos): {path}"
        )
