"""`requirements.txt`, `requirements.lock` y `pyproject.toml` no deben separarse.

CI instala `requirements.lock` (no `pip install -e .`), así que todo lo que el flujo necesita
tiene que estar en `requirements.txt`: si falta, los tests que lo piden **se saltan en
silencio** en vez de fallar (p. ej. `pytest.importorskip("fastapi")`, que hoy afecta a 14
ficheros de test).

`pyproject.toml` es la fuente única de versiones (dependencias base + extras); esta prueba
compara ambos archivos para que no vuelvan a divergir: mismos nombres, mismos specifiers y
mismos marcadores. Además, `requirements.lock` debe fijar exactamente las dependencias
directas con hashes y conservar la resolución universal para Linux/Windows y Python 3.11+.

Los extras `desktop-control*` (pywinauto / pyobjc-framework-* / PyGObject) quedan **fuera a
propósito**: son específicos de plataforma, con import perezoso y con smoke opt-in, y no son
necesarios para ejecutar la suite en CI.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
LOCK = RAIZ / "requirements.lock"

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


_ENTRADA_LOCK = re.compile(
    r"^(?P<nombre>[A-Za-z0-9_.-]+)==(?P<version>[^ ;\\]+)(?P<resto>[^\\]*?)\\?$"
)
_HASH_LOCK = re.compile(r"--hash=sha256:[0-9a-f]{64}\b")


def _lock() -> dict[str, dict[str, object]]:
    """Paquetes fijados en el lock, con marcador y número de hashes por paquete."""
    entradas: dict[str, dict[str, object]] = {}
    actual: dict[str, object] | None = None
    for linea in LOCK.read_text(encoding="utf-8").splitlines():
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        if not linea[0].isspace():
            coincidencia = _ENTRADA_LOCK.match(linea.strip())
            assert coincidencia, f"línea inesperada en requirements.lock: {linea!r}"
            nombre = _normalizar(coincidencia.group("nombre"))
            assert nombre not in entradas, (
                f"paquete duplicado en requirements.lock: {nombre}"
            )
            actual = {
                "version": coincidencia.group("version"),
                "marcador": coincidencia.group("resto").strip(),
                "hashes": 0,
            }
            entradas[nombre] = actual
        elif actual is not None and _HASH_LOCK.search(linea):
            actual["hashes"] = int(actual["hashes"]) + 1
    return entradas


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


def test_requirements_lock_cubre_y_fija_las_dependencias_directas() -> None:
    """El lock que instala CI no puede quedarse corto ni dejar versiones abiertas."""
    requeridas = _requirements()
    lock = _lock()
    faltantes = sorted(set(requeridas) - set(lock))
    assert not faltantes, (
        "requirements.lock no fija dependencias declaradas en requirements.txt: "
        + ", ".join(faltantes)
    )

    sin_hash = sorted(nombre for nombre, datos in lock.items() if not datos["hashes"])
    assert not sin_hash, (
        "requirements.lock debe fijar hashes para todas las distribuciones: "
        + ", ".join(sin_hash)
    )


def test_requirements_lock_conserva_la_resolucion_universal() -> None:
    """El lock debe servir en Linux/Windows y Python 3.11+, no sólo en macOS."""
    cabecera = "\n".join(LOCK.read_text(encoding="utf-8").splitlines()[:2])
    for opcion in (
        "--universal",
        "--python-version 3.11",
        "--generate-hashes",
        "--no-emit-package pip",
    ):
        assert opcion in cabecera, f"falta {opcion!r} en la cabecera de requirements.lock"

    lock = _lock()
    # colorama es una dependencia directa sólo para Windows: si desaparece el
    # marcador, el lock dejaría de ser universal y el CLI fallaría en Windows.
    colorama = lock.get("colorama")
    assert colorama is not None, "requirements.lock no incluye colorama"
    assert "win32" in str(colorama["marcador"]), (
        "requirements.lock perdió el marcador de Windows para colorama"
    )
    # pip es el instalador, no una dependencia de la aplicación: no debe quedar
    # fijado en el lock ni autoactualizarse durante `pip install`.
    assert "pip" not in lock, "requirements.lock no debe fijar el instalador pip"
