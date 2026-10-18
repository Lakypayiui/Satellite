"""Iconos: assets originales, formatos derivados y su uso en Electron/UI.

No hay runner JS en el repo, así que las comprobaciones de interfaz son estáticas
(rutas, marcado y CSS); los formatos derivados se validan por su cabecera real.
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESKTOP = RAIZ / "desktop"


def _cabecera(ruta: Path) -> bytes:
    return ruta.read_bytes()[:8]


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


def test_packaging_icons_derived_from_the_originals() -> None:
    png = DESKTOP / "build/icon.png"
    ico = DESKTOP / "build/icon.ico"
    icns = DESKTOP / "build/icon.icns"
    assert _cabecera(png)[:4] == b"\x89PNG"
    datos_ico = ico.read_bytes()
    assert datos_ico[:4] == b"\x00\x00\x01\x00"  # ICONDIR
    assert int.from_bytes(datos_ico[4:6], "little") >= 4  # varios tamaños
    assert icns.read_bytes()[:4] == b"icns"

    pkg = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    assert pkg["build"]["win"]["icon"] == "build/icon.ico"
    assert pkg["build"]["mac"]["icon"] == "build/icon.icns"
    assert pkg["build"]["linux"]["icon"] == "build/icon.png"


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
