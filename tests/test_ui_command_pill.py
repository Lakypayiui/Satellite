"""Píldora de comandos de la cabecera: texto «Comandos» con el mismo atajo y acción (Ctrl+K)."""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
APP = (RAIZ / "desktop/src/App.tsx").read_text(encoding="utf-8")
I18N = (RAIZ / "desktop/src/i18n.tsx").read_text(encoding="utf-8")


def test_pill_text_is_comandos_with_the_same_shortcut_and_action() -> None:
    ancla = "onClick={() => abrirPaleta()}"
    assert ancla in APP

    inicio = APP.rindex("<button", 0, APP.index(ancla))
    bloque = APP[inicio : APP.index("</button>", inicio)]

    # Misma acción y mismo atajo (en el tooltip/aria, ahora traducidos).
    assert ancla in bloque
    assert 'title={t("chip.commands.title")}' in bloque
    assert 'aria-label={t("chip.commands.aria")}' in bloque

    # Texto visible: «Comandos» (traducido), ya no «Ctrl+K».
    visible = bloque.rsplit(">", 1)[-1].replace("\n", "").strip()
    assert visible == '{t("chip.commands")}'
    assert 'es: "Comandos"' in I18N
    assert "Ctrl+K" in I18N  # el atajo se conserva en el tooltip
