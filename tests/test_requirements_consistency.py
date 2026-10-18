"""`requirements.txt` y `pyproject.toml` no deben separarse.

CI instala `requirements.txt` (no `pip install -e .`), así que todo lo que el flujo necesita
tiene que estar ahí: si falta, los tests que lo piden **se saltan en silencio** en vez de
fallar (p. ej. `pytest.importorskip("fastapi")`, que hoy afecta a 14 ficheros de test).

`pyproject.toml` es la fuente única de versiones (dependencias base + extras); esta prueba
compara ambos archivos para que no vuelvan a divergir: mismos nombres, mismos specifiers y
mismos marcadores.

Los extras `desktop-control*` (pywinauto / pyobjc-framework-* / PyGObject) quedan **fuera a
propósito**: son específicos de plataforma, con import perezoso y con smoke opt-in, y no son
necesarios para ejecutar la suite en CI.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Grupos de pyproject que el flujo de CI necesita ver reflejados en requirements.txt.
GRUPOS_NECESARIOS = ("base", "dev", "desktop")
# Extras que NO deben entrar en requirements.txt (opcionales y por plataforma).
EXTRAS_DE_PLATAFORMA = ("desktop-control", "desktop-control-macos", "desktop-control-linux")
# Dependencias que la suite usa de verdad y cuya ausencia la haría saltar en silencio.
CRITICAS_PARA_LA_SUITE = ("fastapi", "uvicorn", "pydantic", "pytest")


def _normalizar(texto: str) -> str:
    """Nombre/specifier/marcador comparable: sin espacios, comillas ni mayúsculas."""
    return re.sub(r"\s+", "", texto).replace("'", '"').replace("_", "-").lower()


def _partir(entrada: str) -> tuple[str, str]:
    """`nombre` y el resto de la línea (specifier + marcador) por separado."""
    limpia = entrada.split("#")[0].strip()
    nombre = re.split(r"[<>=!;\[( ]", limpia, maxsplit=1)[0].strip()
    return _normalizar(nombre), _normalizar(limpia[len(nombre) :])


def _dictar(entradas) -> dict[str, str]:
    salida: dict[str, str] = {}
    for entrada in entradas:
        nombre, resto = _partir(entrada)
        if nombre:
            salida[nombre] = resto
    return salida


def _requirements() -> dict[str, str]:
    return _dictar(
        (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()
    )


def _pyproject() -> dict[str, dict[str, str]]:
    datos = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    proyecto = datos["project"]
    grupos = {"base": _dictar(proyecto.get("dependencies", []))}
    for extra, dependencias in (proyecto.get("optional-dependencies") or {}).items():
        grupos[extra] = _dictar(dependencias)
    return grupos


def test_requirements_cubre_las_dependencias_que_necesita_ci() -> None:
    requeridas = _requirements()
    grupos = _pyproject()

    faltantes: list[str] = []
    for grupo in GRUPOS_NECESARIOS:
        for nombre, resto in grupos[grupo].items():
            if nombre not in requeridas:
                faltantes.append(f"{nombre} ({grupo})")
            elif requeridas[nombre] != resto:
                # Mismo paquete con specifier/marcador distinto: versiones incoherentes.
                faltantes.append(
                    f"{nombre} ({grupo}): requirements='{requeridas[nombre]}' "
                    f"pyproject='{resto}'"
                )
    assert not faltantes, (
        "requirements.txt (lo que instala CI) no refleja pyproject.toml: "
        + ", ".join(sorted(faltantes))
    )


def test_las_dependencias_criticas_para_la_suite_estan_declaradas() -> None:
    requeridas = _requirements()
    for nombre in CRITICAS_PARA_LA_SUITE:
        assert nombre in requeridas, (
            f"{nombre} falta en requirements.txt: la suite que depende de él se saltaría "
            "en silencio en CI"
        )


def test_requirements_no_declara_nada_ajeno_a_pyproject() -> None:
    """Sin duplicados ni paquetes inventados fuera de pyproject."""
    requeridas = set(_requirements())
    conocidos = set().union(*(set(grupo) for grupo in _pyproject().values()))
    ajenos = sorted(requeridas - conocidos)
    assert not ajenos, (
        "requirements.txt declara paquetes que pyproject.toml no conoce: " + ", ".join(ajenos)
    )


def test_los_extras_de_plataforma_no_entran_en_requirements() -> None:
    """Documenta la exclusión deliberada: son opcionales, por plataforma y opt-in."""
    grupos = _pyproject()
    requeridas = set(_requirements())
    for extra in EXTRAS_DE_PLATAFORMA:
        for nombre in grupos[extra]:
            assert nombre not in requeridas, (
                f"{nombre} ({extra}) es opcional y por plataforma: no debe instalarse en el "
                "job de tests"
            )
