"""Política de seguridad por defecto (deny-by-default) para tools."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from satellite.security.base import SecurityPolicy


@dataclass
class DefaultSecurityPolicy(SecurityPolicy):
    """Deny-by-default: solo ejecuta lo explícitamente permitido.

    - ``allowed_tools``: ids de tools permitidos (vacío = todo denegado).
    - ``allowed_paths``: raíces del filesystem accesibles adicionales a
      ``project_root`` (si está vacío, solo project_root).
    - ``allowed_commands``: binarios/prefixos de comandos permitidos.
    - ``allow_terminal``: habilita el terminal como categoría.
    """

    allowed_tools: set[str] = field(default_factory=set)
    allowed_paths: tuple[Path, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allow_terminal: bool = False

    def check_tool(
        self,
        tool_id: str,
        arguments: dict,
        allowed_tools: set[str] | None = None,
    ) -> bool:
        """True si la tool está permitida.

        La tool debe estar en ``allowed_tools`` (si se pasa) Y en
        ``self.allowed_tools``, o ser ``request_context`` (siempre disponible).
        """
        if tool_id == "request_context":
            return True
        if allowed_tools is not None and tool_id not in allowed_tools:
            return False
        return tool_id in self.allowed_tools

    def check_path(self, path: Path, project_root: Path) -> bool:
        """True si ``path`` resuelto queda dentro de una raíz permitida.

        Neutraliza ``..`` y symlinks con ``Path.resolve()``. El project_root
        siempre está permitido; ``allowed_paths`` añade raíces extra.
        """
        try:
            resolved = path.resolve()
        except OSError:
            return False
        root = project_root.resolve()
        if resolved == root or root in resolved.parents:
            return True
        for allowed in self.allowed_paths:
            base = allowed.resolve()
            if resolved == base or base in resolved.parents:
                return True
        return False

    def check_command(self, argv: list[str]) -> bool:
        """True si el binario del comando está permitido.

        El primer elemento (binario) debe aparecer en ``allowed_commands`` o,
        si es una ruta, quedar dentro de alguno de los ``allowed_paths``.
        """
        if not argv:
            return False
        if not self.allow_terminal:
            return False
        binary = argv[0]
        if binary in self.allowed_commands:
            return True
        if os.sep in binary or "/" in binary:
            try:
                resolved = Path(binary).resolve()
            except OSError:
                return False
            for allowed in self.allowed_paths:
                base = allowed.resolve()
                if resolved == base or base in resolved.parents:
                    return True
        return False
