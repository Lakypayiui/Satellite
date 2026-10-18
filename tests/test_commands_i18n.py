"""Catálogo de comandos localizado: cobertura completa del backend y los tres consumidores.

`satellite/commands.py` sigue siendo la única definición (y la fuente en español). Aquí no hay
runner JS: se leen `desktop/src/commandsI18n.ts` (mapa id → claves) y `desktop/src/i18n.tsx`
(diccionario `TEXTOS`) con regex y se comparan con el `CATALOG` real, comando a comando.
"""

from __future__ import annotations

import re
from pathlib import Path

from satellite.commands import CATALOG

RAIZ = Path(__file__).resolve().parents[1]
DESKTOP = RAIZ / "desktop"

# Una entrada de `COMANDOS_TEXTO`: empieza en el nivel superior del objeto (`  "id": {`) y termina
# en el cierre con la misma indentación (`  },`). Así el cuerpo puede contener el bloque `args`.
RE_ENTRADA = re.compile(r'^  "(?P<id>[\w-]+)":\s*\{(?P<cuerpo>.*?)^  \},', re.S | re.M)
RE_CLAVE_SUMMARY = re.compile(r'^\s*summary:\s*"(?P<clave>[^"]+)"', re.M)
RE_CLAVE_CATEGORY = re.compile(r'^\s*category:\s*"(?P<clave>[^"]+)"', re.M)
RE_BLOQUE_ARGS = re.compile(r'^\s*args:\s*\{(?P<cuerpo>.*?)^\s{4}\},', re.S | re.M)
RE_ARG = re.compile(r'^      "(?P<key>[\w-]+)":\s*\{(?P<cuerpo>[^\n]*?)\},?\s*$', re.M)
RE_ARG_LABEL = re.compile(r'label:\s*"(?P<clave>[^"]+)"')
RE_ARG_PLACEHOLDER = re.compile(r'placeholder:\s*"(?P<clave>[^"]+)"')

# `"clave": { es: "…", en: "…" }`. El valor puede llevar llaves (`sandbox {n}`) y saltos de línea,
# por eso se delimita por comillas (admitiendo comillas escapadas) y no por `.*?` hasta una `}`.
RE_PAR = re.compile(
    r'"(?P<clave>[^"\n]+)":\s*\{\s*es:\s*"(?P<es>(?:[^"\\]|\\.)*)"\s*,'
    r'\s*en:\s*"(?P<en>(?:[^"\\]|\\.)*)"\s*,?\s*\}',
    re.S,
)

# Resúmenes cuyo texto es puramente técnico (nombres de rol): es y en pueden coincidir.
RESUMENES_TECNICOS = {"roles"}


def _leer(rel: str) -> str:
    return (DESKTOP / rel).read_text(encoding="utf-8")


def _pares(i18n: str) -> dict[str, tuple[str, str]]:
    """Claves de `TEXTOS` → (es, en), sin duplicados."""
    pares: dict[str, tuple[str, str]] = {}
    for coincidencia in RE_PAR.finditer(i18n):
        clave = coincidencia.group("clave")
        assert clave not in pares, f"clave duplicada en TEXTOS: {clave}"
        pares[clave] = (
            coincidencia.group("es").replace('\\"', '"'),
            coincidencia.group("en").replace('\\"', '"'),
        )
    return pares


def _comandos(comandos_ts: str) -> dict[str, dict]:
    """`COMANDOS_TEXTO` → id: {summary, category, args: {key: {label, placeholder}}}."""
    entradas: dict[str, dict] = {}
    for coincidencia in RE_ENTRADA.finditer(comandos_ts):
        identificador = coincidencia.group("id")
        cuerpo = coincidencia.group("cuerpo")
        resumen = RE_CLAVE_SUMMARY.search(cuerpo)
        categoria = RE_CLAVE_CATEGORY.search(cuerpo)
        assert resumen, f"{identificador}: falta `summary` en la entrada"
        assert categoria, f"{identificador}: falta `category` en la entrada"
        assert identificador not in entradas, f"id duplicado en COMANDOS_TEXTO: {identificador}"

        args: dict[str, dict[str, str]] = {}
        bloque = RE_BLOQUE_ARGS.search(cuerpo)
        if bloque:
            for arg in RE_ARG.finditer(bloque.group("cuerpo")):
                etiqueta = RE_ARG_LABEL.search(arg.group("cuerpo"))
                marcador = RE_ARG_PLACEHOLDER.search(arg.group("cuerpo"))
                args[arg.group("key")] = {
                    "label": etiqueta.group("clave") if etiqueta else "",
                    "placeholder": marcador.group("clave") if marcador else "",
                }

        entradas[identificador] = {
            "summary": resumen.group("clave"),
            "category": categoria.group("clave"),
            "args": args,
        }
    return entradas


def _entradas() -> dict[str, dict]:
    return _comandos(_leer("src/commandsI18n.ts"))


def _diccionario() -> dict[str, tuple[str, str]]:
    return _pares(_leer("src/i18n.tsx"))


def test_el_catalogo_no_esta_vacio() -> None:
    # Guarda contra una prueba vacía: el catálogo real tiene decenas de comandos.
    assert len(CATALOG) >= 50, len(CATALOG)
    assert len({spec.id for spec in CATALOG}) == len(CATALOG)


def test_comandos_texto_cubre_todo_el_catalogo() -> None:
    entradas = _entradas()
    ids = [spec.id for spec in CATALOG]
    faltan = sorted(set(ids) - set(entradas))
    sobran = sorted(set(entradas) - set(ids))
    assert not faltan, f"ids sin entrada en COMANDOS_TEXTO: {faltan}"
    assert not sobran, f"entradas de ids que no existen en el catálogo: {sobran}"
    assert len(entradas) == len(ids), (len(entradas), len(ids))


def test_resumenes_y_categorias_exactos_y_con_ambos_idiomas() -> None:
    i18n = _leer("src/i18n.tsx")
    pares = _pares(i18n)
    entradas = _entradas()

    for spec in CATALOG:
        entrada = entradas[spec.id]
        # La entrada apunta a las claves convenidas: `cmd.<id>.summary` y una categoría compartida.
        assert entrada["summary"] == f"cmd.{spec.id}.summary", spec.id
        assert entrada["category"].startswith("cmd.category."), spec.id

        for campo, esperado in (("summary", spec.summary), ("category", spec.category)):
            clave = entrada[campo]
            assert clave in pares, f"{clave}: no está en TEXTOS"
            es, en = pares[clave]
            # `es` es exactamente la cadena del backend (la UI en español no cambia).
            assert es == esperado, f"{clave}: es={es!r} != backend {esperado!r}"
            assert es.strip(), f"{clave}: es vacío"
            assert en.strip(), f"{clave}: en vacío"
            assert i18n.count(f'"{clave}"') == 1, f"{clave}: declarada más de una vez"

    # Las siete categorías del backend, con su clave compartida.
    for slug in ("project", "conversation", "agents", "tasks", "providers", "security", "app"):
        assert f'"cmd.category.{slug}"' in i18n, slug


def test_campos_argspec_cubiertos_y_exactos() -> None:
    pares = _diccionario()
    entradas = _entradas()

    for spec in CATALOG:
        entrada = entradas[spec.id]
        claves = {arg.key for arg in spec.args}
        assert set(entrada["args"]) == claves, (
            spec.id,
            sorted(set(entrada["args"])),
            sorted(claves),
        )
        for arg in spec.args:
            campo = entrada["args"][arg.key]

            clave_label = campo["label"]
            assert clave_label == f"cmd.{spec.id}.arg.{arg.key}.label", (spec.id, arg.key)
            assert clave_label in pares, f"{clave_label}: no está en TEXTOS"
            es_label, en_label = pares[clave_label]
            assert es_label == arg.label, f"{clave_label}: es={es_label!r} != backend {arg.label!r}"
            assert en_label.strip(), f"{clave_label}: en vacío"

            if arg.placeholder:
                clave_ph = campo["placeholder"]
                assert clave_ph == f"cmd.{spec.id}.arg.{arg.key}.placeholder", (spec.id, arg.key)
                assert clave_ph in pares, f"{clave_ph}: no está en TEXTOS"
                es_ph, en_ph = pares[clave_ph]
                assert es_ph == arg.placeholder, f"{clave_ph}: es={es_ph!r} != {arg.placeholder!r}"
                assert en_ph.strip(), f"{clave_ph}: en vacío"
            else:
                # Sin marcador en el backend no se declara clave: no se inventa texto.
                assert campo["placeholder"] == "", (spec.id, arg.key)


def test_ingles_presente_y_distinto_salvo_lo_tecnico() -> None:
    pares = _diccionario()
    entradas = _entradas()

    for spec in CATALOG:
        resumen = entradas[spec.id]["summary"]
        es, en = pares[resumen]
        assert en.strip(), f"{resumen}: en vacío"
        if spec.id not in RESUMENES_TECNICOS:
            assert en != es, f"{resumen}: el inglés repite el español ({es!r})"

        campo = entradas[spec.id]["category"]
        es_cat, en_cat = pares[campo]
        assert en_cat.strip(), f"{campo}: en vacío"
        assert en_cat != es_cat, f"{campo}: el inglés repite el español ({es_cat!r})"

    # Todos los campos: el inglés nunca queda vacío.
    for spec in CATALOG:
        for campo in entradas[spec.id]["args"].values():
            assert pares[campo["label"]][1].strip(), (spec.id, campo["label"])


def test_el_helper_no_traduce_lo_tecnico() -> None:
    comandos_ts = _leer("src/commandsI18n.ts")
    # El helper copia el comando del backend y sólo sustituye los textos visibles.
    assert "...comando" in comandos_ts, "el helper debe partir del comando del backend"
    assert not re.search(r"\busage\s*:", comandos_ts), "el `usage` no se traduce ni se copia a mano"
    assert "summary:" in comandos_ts and "category:" in comandos_ts
    assert "label:" in comandos_ts and "placeholder:" in comandos_ts


def test_respaldo_para_ids_desconocidos() -> None:
    comandos_ts = _leer("src/commandsI18n.ts")
    assert "if (!texto" in comandos_ts, "falta el respaldo cuando el id no está en COMANDOS_TEXTO"
    assert "return comando;" in comandos_ts, "el respaldo debe devolver el comando del backend tal cual"


def test_los_tres_consumidores_usan_el_helper() -> None:
    for nombre in ("CommandPalette.tsx", "Chat.tsx", "App.tsx"):
        fuente = _leer(f"src/{nombre}")
        assert 'from "./commandsI18n"' in fuente, nombre
        assert "comandoTraducido(" in fuente, nombre
        # Se traduce con el `t` del sistema i18n existente (no hay un segundo sistema).
        assert re.search(r"comandoTraducido\(comando, t\)", fuente), nombre
        assert "useT(" in fuente or "useI18n(" in fuente, nombre

    # La paleta traduce el catálogo antes de buscar/agrupar/pintar.
    paleta = _leer("src/CommandPalette.tsx")
    assert "(catalogo ?? []).map((comando) => comandoTraducido(comando, t))" in paleta
    # El autocompletado del chat filtra sobre la lista traducida.
    chat = _leer("src/Chat.tsx")
    assert "(comandos ?? []).map((comando) => comandoTraducido(comando, t))" in chat
    assert "const lista = comandosTraducidos;" in chat
    # El volcado `/commands` y la ayuda de la app pintan el resumen traducido.
    app = _leer("src/App.tsx")
    assert "comandoTraducido(comando, t).summary" in app
