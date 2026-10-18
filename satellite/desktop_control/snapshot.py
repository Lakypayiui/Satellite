"""Serialización compacta de snapshots (§3) y modo diff (§3).

Reglas:
- Sólo se renderizan elementos **visibles + habilitados + interactuables**.
- Refs efímeras: ``[e12] Button "Guardar"``.
- El valor de un campo sensible **nunca** se muestra.
- Topes configurables de elementos y de caracteres (marca de truncado).
- ``mode="diff"`` contra la observación anterior; sin cambios ⇒ ``(sin cambios)``.
- ``quality`` (good|poor) con motivo: una ventana sin elementos operables se
  marca ``poor`` en vez de fingir una observación útil.

Todo texto de la UI pasa por ``satellite.retrieval.sanitize`` (delimitadores
``<data>`` neutralizados, control eliminado): es dato no confiable. No se altera
el comportamiento de ese módulo; sólo se reutiliza.
"""

from __future__ import annotations

from collections.abc import Iterable

from satellite.retrieval.sanitize import sanitize

from .base import Element, ObserveLimits, Snapshot, WindowInfo


def _one_line(value: object) -> str:
    """Texto saneado en una sola línea (sin control, sin saltos, sin ``<data>``)."""
    cleaned = sanitize(value, max_chars=None, collapse=True)
    return " ".join(cleaned.split())


def render_element(element: Element) -> str:
    """``[e12] Button "Guardar"`` (+ valor, o marca de campo sensible)."""
    parts = [f"[{element.ref}] {_one_line(element.role)}"]
    name = _one_line(element.name)
    if name:
        parts.append(f'"{name}"')
    if element.sensitive:
        parts.append("(sensible: valor oculto)")
    elif element.value:
        parts.append(f'= "{_one_line(element.value)}"')
    return " ".join(parts)


def _window_header(window: WindowInfo) -> str:
    title = _one_line(window.title)
    app = _one_line(window.app)
    suffix = f" · {app}" if app else ""
    active = " · activa" if window.active else ""
    return f'ventana: "{title}"{suffix}{active}'


def _diff_lines(previous: Snapshot, operable: tuple[Element, ...]) -> list[str]:
    """Líneas cambiadas/añadidas/quitadas respecto a la observación anterior."""
    prev = {e.ref: render_element(e) for e in previous.operable_elements()}
    cur = {e.ref: render_element(e) for e in operable}
    lines: list[str] = []
    for element in operable:
        line = cur[element.ref]
        if element.ref not in prev:
            lines.append(f"+ {line}")
        elif prev[element.ref] != line:
            lines.append(f"~ {line}")
    for element in previous.operable_elements():
        if element.ref not in cur:
            lines.append(f"- {prev[element.ref]}")
    if not lines:
        lines.append("(sin cambios)")
    return lines


def build_snapshot(
    *,
    snapshot_id: str,
    window: WindowInfo,
    elements: Iterable[Element],
    limits: ObserveLimits | None = None,
    previous: Snapshot | None = None,
    quality: str | None = None,
    reason: str = "",
) -> Snapshot:
    """Construye el snapshot compacto a partir del árbol observado."""
    topes = limits or ObserveLimits()
    observed = tuple(elements)
    operable = tuple(e for e in observed if e.is_operable)

    # El modo diff sólo tiene sentido con una observación anterior.
    mode = topes.mode if previous is not None else "full"
    if mode == "diff" and previous is not None:
        lines = _diff_lines(previous, operable)
    else:
        lines = [render_element(e) for e in operable]

    truncated = False
    discarded = 0
    if len(lines) > topes.max_elements:
        discarded = len(lines) - topes.max_elements
        lines = lines[: topes.max_elements]
        truncated = True

    text = "\n".join([_window_header(window), *lines])
    if truncated:
        text += f"\n… (+{discarded} elementos no mostrados)"
    if len(text) > topes.max_chars:
        text = text[: topes.max_chars] + "\n[truncado]"
        truncated = True

    if quality is None:
        if not operable:
            quality = "poor"
            reason = reason or "sin elementos interactuables visibles"
        else:
            quality = "good"

    return Snapshot(
        snapshot_id=snapshot_id,
        window=window,
        elements=observed,
        text=text,
        quality=quality,
        reason=reason,
        mode=mode,
        previous_snapshot_id=(
            previous.snapshot_id if (previous is not None and mode == "diff") else ""
        ),
        truncated=truncated,
    )
