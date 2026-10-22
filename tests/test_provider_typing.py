"""Regresiones de tipado: las anotaciones de proveedor deben ser resolubles."""

import ast
from pathlib import Path

from satellite.providers import anthropic_provider, openai_compat

_MODULES = (anthropic_provider, openai_compat)


def _type_checking_imports(module) -> set[tuple[str, str]]:
    """Imports declarados dentro de un bloque ``if TYPE_CHECKING``."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[tuple[str, str]] = set()
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"):
            continue
        for child in node.body:
            if isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    imports.add((child.module or "", alias.name))
    return imports


def test_provider_config_is_imported_for_type_checking() -> None:
    """``ProviderConfig`` se usa en anotaciones, así que debe existir para el checker."""
    expected = ("satellite.providers.factory", "ProviderConfig")
    for module in _MODULES:
        assert expected in _type_checking_imports(module)
