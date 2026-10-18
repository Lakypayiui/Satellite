"""i18n de la interfaz: default español, persistencia, cambio en vivo y cobertura.

Sin runner JS: se comprueba el patrón real (diccionario tipado + preferencia persistida).
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESKTOP = RAIZ / "desktop"


def _leer(rel: str) -> str:
    return (DESKTOP / rel).read_text(encoding="utf-8")


def test_default_es_espanol_sin_preferencia() -> None:
    temas = _leer("electron/themes.ts")
    assert 'DEFAULT_IDIOMA: IdiomaId = "es"' in temas
    preload = _leer("electron/preload.ts")
    assert 'initialLanguage: idiomaInicial || "es"' in preload


def test_persistencia_en_las_preferencias_existentes() -> None:
    main = _leer("electron/main.ts")
    assert "language: idiomaActual" in main  # misma vista de preferencias
    assert "satellite:preferences-set-language" in main
    assert "isIdioma" in main and "guardarPreferencias" in main
    assert "leerIdioma" in main

    preload = _leer("electron/preload.ts")
    assert "initialLanguage" in preload and "setLanguage" in preload

    api = _leer("src/api.ts")
    assert "initialLanguage" in api and "guardarIdioma" in api and "setLanguage" in api


def test_cambio_en_vivo_y_sincronizacion_inicial() -> None:
    i18n = _leer("src/i18n.tsx")
    assert "I18nProvider" in i18n and "useI18n" in i18n
    assert "setIdioma" in i18n and "sincronizarIdioma" in i18n
    assert "initialLanguage" in i18n

    app = _leer("src/App.tsx")
    assert "useI18n" in app
    assert "sincronizarIdioma(preferencia.language)" in app
    assert "idioma={idioma}" in app and "onIdioma={cambiarIdioma}" in app

    settings = _leer("src/Settings.tsx")
    assert "IDIOMAS.map" in settings and "props.onIdioma" in settings


def test_cobertura_de_claves_con_ambos_idiomas() -> None:
    i18n = _leer("src/i18n.tsx")
    # El diccionario se declara tipado: `satisfies Record<string, Par>` obliga a `es` y `en`.
    assert "satisfies Record<string, Par>" in i18n

    bloques = re.findall(r'"([^"]+)":\s*\{(.*?)\}\s*,\s*\n', i18n, re.S)
    assert len(bloques) >= 60, f"pocas claves: {len(bloques)}"
    for clave, cuerpo in bloques:
        assert re.search(r"\bes:", cuerpo), f"falta 'es' en {clave}"
        assert re.search(r"\ben:", cuerpo), f"falta 'en' en {clave}"

    # No debe quedar ningún idioma vacío.
    assert 'es: ""' not in i18n and 'en: ""' not in i18n


def test_bloque_ajustes_usa_claves_i18n() -> None:
    settings = _leer("src/Settings.tsx")
    editor = _leer("src/ModelEditor.tsx")

    # Ambos componentes usan el sistema tipado, no otro.
    assert "useT(" in settings and "useT(" in editor
    assert 'from "./i18n"' in editor

    # Cobertura amplia de las páginas internas de Ajustes y del editor.
    assert settings.count('t("settings.') >= 80
    assert editor.count('t("editor.') >= 40

    # Las claves usadas existen en el diccionario y tienen ambos idiomas.
    i18n = _leer("src/i18n.tsx")
    usadas = set(re.findall(r't\("([A-Za-z0-9.]+)"', settings + editor))
    declaradas = set(re.findall(r'"([A-Za-z0-9.]+)":\s*\{', i18n))
    faltan = sorted(usadas - declaradas)
    assert not faltan, f"claves sin declarar: {faltan[:10]}"

    for clave in (
        "settings.models.add",
        "settings.roles.apply",
        "settings.permissions.safeMode",
        "settings.desktop.toggle",
        "settings.maintenance.doctor",
        "settings.advanced.meta",
        "editor.section.general",
    ):
        assert f'"{clave}"' in i18n, clave


def test_bloque_chat_panel_usa_claves_i18n() -> None:
    chat = _leer("src/Chat.tsx")
    panel = _leer("src/RightPanel.tsx")

    # Usan el sistema tipado existente.
    assert "useT(" in chat and "useT(" in panel
    assert "from \"./i18n\"" in chat and "from \"./i18n\"" in panel

    # Volumen de textos traducidos (hay claves usadas vía mapas, no sólo literales).
    assert chat.count("t(") >= 20
    assert panel.count("t(") >= 30

    # Toda clave literal usada existe en el diccionario (y el diccionario ya exige es+en).
    i18n = _leer("src/i18n.tsx")
    usadas = set(re.findall(r't\("([A-Za-z0-9.]+)"', chat + panel))
    declaradas = set(re.findall(r'"([A-Za-z0-9.]+)":\s*\{', i18n))
    faltan = sorted(usadas - declaradas)
    assert not faltan, f"claves sin declarar: {faltan[:10]}"

    # El agua del chat sigue intacta (no se tocó el comportamiento).
    assert "props.marca" in chat and "chat-marca" in chat


def test_bloque_final_usa_i18n_y_sin_textos_fijos() -> None:
    componentes = (
        "App.tsx",
        "Chat.tsx",
        "RightPanel.tsx",
        "Settings.tsx",
        "ModelEditor.tsx",
        "Onboarding.tsx",
        "Sidebar.tsx",
        "CommandPalette.tsx",
        "QuickThink.tsx",
        "TitleBar.tsx",
        "Splash.tsx",
    )
    for nombre in componentes:
        fuente = _leer(f"src/{nombre}")
        assert "useT(" in fuente or "useI18n(" in fuente, nombre
        assert "t(" in fuente, nombre

    # Etiquetas de evento visibles: claves tipadas (sin texto fijo en api.ts).
    api = _leer("src/api.ts")
    assert "ETIQUETA_EVENTO: Record<string, Clave>" in api
    assert 'import type { Clave } from "./i18n"' in api

    i18n = _leer("src/i18n.tsx")
    eventos = set(re.findall(r'"event\.([A-Za-z0-9_.]+)":', i18n))
    assert len(eventos) >= 20
    assert "unknown" in eventos

    # Guarda contra texto UI fijo en español en atributos visibles. Los valores técnicos
    # (ids, rutas, atajos, tokens) no coinciden con esta lista de palabras de interfaz.
    prohibidas = re.compile(
        r'(?:title|aria-label|placeholder|aria-valuetext)="[^"]*\b'
        r"(?:Guardar|Cerrar|Cancelar|Abrir|Nuevo|Nueva|Buscar|Enviar|Volver|Ajustes|"
        r"Proveedor|Modelo|Permisos|Tareas|Trabajos|Contexto|Cargando|Error|Idioma|"
        r"Apariencia|Eliminar|Quitar|Renombrar|Archivar|Fijar|Detener|Ejecutar)\b",
        re.IGNORECASE,
    )
    for nombre in componentes:
        fuente = _leer(f"src/{nombre}")
        encontrados = prohibidas.findall(fuente)
        assert not encontrados, f"{nombre}: texto UI fijo en español {encontrados[:5]}"


def test_componentes_principales_usan_claves_i18n() -> None:
    # Componentes ya localizados: usan el i18n tipado, no literales sueltos.
    for componente in ("Sidebar.tsx", "CommandPalette.tsx", "Splash.tsx", "Settings.tsx"):
        fuente = _leer(f"src/{componente}")
        assert "useT(" in fuente or "useI18n(" in fuente, componente

    app = _leer("src/App.tsx")
    assert "useI18n(" in app

    # Categorías de Ajustes: cada una resuelve su título por clave i18n.
    settings = _leer("src/Settings.tsx")
    assert "t(categoria.clave)" in settings and "t(categoria.resumenClave)" in settings
    for clave in (
        "cat.modelos",
        "cat.roles",
        "cat.permisos",
        "cat.escritorio",
        "cat.mantenimiento",
        "cat.avanzado",
        "cat.apariencia",
    ):
        assert f'"{clave}"' in _leer("src/i18n.tsx"), clave
