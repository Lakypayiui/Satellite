"""Guardas estáticas de la píldora de modelo (no hay runner JS en el repo).

Comprueban que la píldora conserva el texto y el tamaño compactos originales, y que el
popover sigue siendo accesible, sin depender de un navegador.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1] / "desktop" / "src"


def test_pill_label_is_the_compact_provider_model_text() -> None:
    texto = (RAIZ / "QuickThink.tsx").read_text(encoding="utf-8")
    inicio = texto.index("<button")
    fin = texto.index("</button>", inicio)
    bloque = texto[inicio:fin]

    # Texto original: sólo «proveedor · modelo» (sin resumen de think/effort ni flecha extra).
    assert "{etiqueta}" in bloque
    assert "resumen" not in bloque
    assert "▾" not in bloque
    # Sigue siendo accesible.
    assert "aria-haspopup" in bloque
    assert "aria-label" in bloque
    assert "title" in bloque


def test_pill_keeps_the_compact_max_width() -> None:
    css = (RAIZ / "styles.css").read_text(encoding="utf-8")
    inicio = css.index(".quick-think > .chip-sel {")
    fin = css.index("}", inicio)
    bloque = css[inicio:fin]

    assert "max-width: 280px" in bloque
    assert "white-space: nowrap" in bloque
    assert "text-overflow: ellipsis" in bloque
