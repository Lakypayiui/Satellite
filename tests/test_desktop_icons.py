"""Iconos: assets originales, formatos derivados y su uso en Electron/UI.

No hay runner JS en el repo, así que las comprobaciones de interfaz son estáticas
(rutas, marcado y CSS); los formatos derivados se validan por su contenido real.

``desktop/build/`` está **ignorado por git** (son artefactos de empaquetado), así que un
checkout limpio no los trae. El test no depende de que alguien haya ejecutado un comando a
mano: el fixture ``derivados`` los genera con ``npm run icons`` (Node puro, headless) si
faltan o están viejos, y luego se validan los ficheros de verdad.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
DESKTOP = RAIZ / "desktop"
SALIDA = DESKTOP / "build"
DERIVADOS = ("icon.png", "icon.ico", "icon.icns")
FUENTES = (RAIZ / "Icono/Ico_white", RAIZ / "Icono/Ico_Black")


def _cabecera(ruta: Path) -> bytes:
    return ruta.read_bytes()[:8]


def _lado_png(datos: bytes, desplazamiento: int = 0) -> tuple[int, int]:
    """Anchura y altura de un PNG, leídas de su IHDR (sin dependencias)."""
    assert datos[desplazamiento : desplazamiento + 8] == b"\x89PNG\r\n\x1a\n"
    ancho = int.from_bytes(datos[desplazamiento + 16 : desplazamiento + 20], "big")
    alto = int.from_bytes(datos[desplazamiento + 20 : desplazamiento + 24], "big")
    return ancho, alto


def _generar_iconos() -> None:
    """Genera los derivados con el script del repo (Node puro, sin Electron ni display)."""
    node = shutil.which("node")
    if node is None:
        pytest.fail(
            "hace falta Node para generar los iconos del empaquetado: desktop/build/ no se "
            "versiona y el generador (desktop/scripts/icons.mjs) es Node puro. Instala Node "
            "o ejecuta 'npm run icons' en desktop/ antes de las pruebas."
        )
    proceso = subprocess.run(
        [node, "scripts/icons.mjs"],
        cwd=DESKTOP,
        capture_output=True,
        text=True,
    )
    if proceso.returncode != 0:
        pytest.fail(
            f"'node scripts/icons.mjs' falló ({proceso.returncode}):\n"
            f"{proceso.stdout}\n{proceso.stderr}"
        )


@pytest.fixture(scope="module")
def derivados() -> Path:
    """Asegura que los derivados existen y están al día (los genera si hace falta)."""
    faltan = [nombre for nombre in DERIVADOS if not (SALIDA / nombre).is_file()]
    reciente = max(f.stat().st_mtime for f in FUENTES)
    viejos = [
        nombre
        for nombre in DERIVADOS
        if (SALIDA / nombre).is_file() and (SALIDA / nombre).stat().st_mtime < reciente
    ]
    if faltan or viejos:
        _generar_iconos()
    return SALIDA


def test_original_icons_are_png_and_the_app_copies_are_untouched() -> None:
    for nombre in ("Ico_Black", "Ico_white"):
        original = RAIZ / "Icono" / nombre
        assert original.is_file()
        assert _cabecera(original)[:4] == b"\x89PNG"

    # Las copias que usa la app son idénticas al arte original (no se retoca).
    assert (DESKTOP / "src/assets/satellite-white.png").read_bytes() == (
        RAIZ / "Icono/Ico_white"
    ).read_bytes()
    assert (DESKTOP / "src/assets/satellite-black.png").read_bytes() == (
        RAIZ / "Icono/Ico_Black"
    ).read_bytes()


def test_packaging_icons_derived_from_the_originals(derivados: Path) -> None:
    png = derivados / "icon.png"
    ico = derivados / "icon.ico"
    icns = derivados / "icon.icns"

    # PNG cuadrado de 512, tal y como espera el empaquetado de Linux.
    datos_png = png.read_bytes()
    assert _lado_png(datos_png) == (512, 512)

    # ICO: cabecera ICONDIR, varios tamaños y cada PNG embebido con el tamaño que declara.
    datos_ico = ico.read_bytes()
    assert datos_ico[:4] == b"\x00\x00\x01\x00"
    entradas = int.from_bytes(datos_ico[4:6], "little")
    assert entradas >= 4  # multinivel
    for indice in range(entradas):
        base = 6 + indice * 16
        ancho = datos_ico[base] or 256
        alto = datos_ico[base + 1] or 256
        largo = int.from_bytes(datos_ico[base + 8 : base + 12], "little")
        desplazamiento = int.from_bytes(datos_ico[base + 12 : base + 16], "little")
        assert _lado_png(datos_ico[desplazamiento : desplazamiento + largo]) == (ancho, alto)

    # ICNS: contenedor real y cada entrada con el tamaño de su tipo.
    datos_icns = icns.read_bytes()
    assert datos_icns[:4] == b"icns"
    assert int.from_bytes(datos_icns[4:8], "big") == len(datos_icns)
    esperado = {"icp4": 16, "icp5": 32, "icp6": 64, "ic07": 128, "ic08": 256, "ic09": 512, "ic10": 1024}
    vistas: list[str] = []
    posicion = 8
    while posicion < len(datos_icns):
        tipo = datos_icns[posicion : posicion + 4].decode("ascii")
        largo = int.from_bytes(datos_icns[posicion + 4 : posicion + 8], "big")
        assert _lado_png(datos_icns[posicion + 8 : posicion + 8 + largo]) == (
            esperado[tipo],
            esperado[tipo],
        )
        vistas.append(tipo)
        posicion += largo
    assert vistas == list(esperado)

    # Las variantes claras se generan igual (arte para tema claro).
    for sufijo in ("-light.png", "-light.ico", "-light.icns"):
        assert (derivados / f"icon{sufijo}").is_file()

    # El empaquetado apunta a esos ficheros generados.
    pkg = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    assert pkg["build"]["win"]["icon"] == "build/icon.ico"
    assert pkg["build"]["mac"]["icon"] == "build/icon.icns"
    assert pkg["build"]["linux"]["icon"] == "build/icon.png"
    # Y el generador es Node puro: sin Electron no hace falta display (ni xvfb en CI).
    assert pkg["scripts"]["icons"] == "node scripts/icons.mjs"
    assert "npm run icons" in pkg["scripts"]["dist"]


def test_electron_icon_follows_the_os_theme_with_the_originals() -> None:
    main = (DESKTOP / "electron/main.ts").read_text(encoding="utf-8")
    assert "Icono" in main
    # Los dos originales: arte claro y arte oscuro.
    assert "Ico_white" in main and "Ico_Black" in main
    # Elige variante por el tema del SO y la refresca en vivo cuando cambia.
    assert "nativeTheme" in main and "shouldUseDarkColors" in main
    assert 'nativeTheme.on("updated"' in main
    assert "icon: iconoSegunSistema()" in main
    assert "app.dock?.setIcon" in main


def test_ui_shows_icon_with_text_watermark_and_splash() -> None:
    titulo = (DESKTOP / "src/TitleBar.tsx").read_text(encoding="utf-8")
    assert "Logo" in titulo and "barra-logo" in titulo

    chat = (DESKTOP / "src/Chat.tsx").read_text(encoding="utf-8")
    assert "chat-marca" in chat and "Logo" in chat

    app = (DESKTOP / "src/App.tsx").read_text(encoding="utf-8")
    assert "Splash" in app and "mostrarSplash" in app

    css = (DESKTOP / "src/styles.css").read_text(encoding="utf-8")
    assert ".barra-logo" in css and ".splash" in css
    # La marca de agua no captura clics, no se selecciona y no ocupa espacio (altura 0).
    inicio = css.index(".chat-marca {")
    bloque = css[inicio : css.index("}", inicio)]
    assert "pointer-events: none" in bloque
    assert "height: 0" in bloque
    assert "user-select: none" in bloque
