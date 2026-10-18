"""Marca de agua del chat: preferencias, rango y controles de Apariencia.

No hay runner JS, así que se comprueban rutas/estado/persistencia a nivel de fuente
(igual que el resto de pruebas de interfaz del repo).
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESKTOP = RAIZ / "desktop"


def _leer(rel: str) -> str:
    return (DESKTOP / rel).read_text(encoding="utf-8")


def test_defaults_y_rango_en_el_catalogo_de_apariencia() -> None:
    temas = _leer("electron/themes.ts")
    assert "DEFAULT_CHAT_WATERMARK = true" in temas
    assert "DEFAULT_CHAT_WATERMARK_OPACITY = 0.06" in temas
    assert "CHAT_WATERMARK_OPACITY_MIN = 0.01" in temas
    assert "CHAT_WATERMARK_OPACITY_MAX = 0.2" in temas
    assert "CHAT_WATERMARK_OPACITY_STEP = 0.01" in temas
    assert "function clampWatermarkOpacity" in temas


def test_persistencia_usa_las_preferencias_existentes() -> None:
    main = _leer("electron/main.ts")
    assert "chat_watermark" in main and "chat_watermark_opacity" in main
    assert "satellite:preferences-set-chat-watermark" in main
    assert "satellite:preferences-set-chat-watermark-opacity" in main
    assert "guardarPreferencias" in main  # mismo archivo de preferencias, sin almacén paralelo
    assert "clampWatermarkOpacity" in main

    preload = _leer("electron/preload.ts")
    assert "initialWatermark" in preload and "initialWatermarkOpacity" in preload
    assert "setWatermark" in preload and "setWatermarkOpacity" in preload


def test_estado_inicial_y_actualizacion_en_vivo() -> None:
    app = _leer("src/App.tsx")
    assert "initialWatermark" in app and "initialWatermarkOpacity" in app
    assert "clampWatermarkOpacity" in app
    assert "--chat-marca-opacidad" in app

    # Se aplica antes de pintar (sin parpadeo) y también desde el puente.
    assert "--chat-marca-opacidad" in _leer("src/main.tsx")

    chat = _leer("src/Chat.tsx")
    assert "props.marca" in chat and "chat-marca" in chat


def test_controles_de_apariencia_estado_y_accesibilidad() -> None:
    settings = _leer("src/Settings.tsx")
    assert 't("settings.appearance.watermark.show")' in settings
    assert _leer("src/i18n.tsx").count("mostrar el logo de Satellite en los chats") == 1
    assert 'type="range"' in settings
    assert "disabled={!props.marca}" in settings  # oculto ⇒ deshabilitado, pero conserva el valor
    assert 't("settings.appearance.watermark.opacity.aria")' in settings
    assert "aria-valuetext" in settings
    assert "CHAT_WATERMARK_OPACITY_MIN" in settings
    assert "CHAT_WATERMARK_OPACITY_MAX" in settings
    assert "Number(evento.target.value) / 100" in settings
    assert "%" in settings  # porcentaje visible


def test_la_opacidad_no_cambia_por_tema() -> None:
    css = _leer("src/styles.css")
    assert "opacity: var(--chat-marca-opacidad" in css
    # Sin overrides por tema: el valor elegido se respeta en claro y oscuro.
    assert '[data-theme="claro"] .chat-marca-logo' not in css
