"""Detección explícita de peticiones de diagrama (para enrutar a Pele).

Regla determinista, español e inglés. Sólo dispara cuando el mensaje pide de verdad
un diagrama (``dibuja un diagrama de…``), cuando pide **diseñar/proponer** una
arquitectura de software/sistema, o cuando el propio mensaje ya trae fuente Mermaid.
No dispara para trabajo no relacionado: explicar o documentar la arquitectura
existente, arreglar un bug, crear un README, etc.

La detección es *explícita* a propósito: la capacidad es condicional (sólo se usa
cuando encaja) y no queremos gastar una llamada al planificador —ni cargar un Node
que puede faltar— en peticiones ajenas.
"""

from __future__ import annotations

import re

# Sustantivos que designan un diagrama (tipo de diagrama o "esquema/gráfico").
_DIAGRAM_TYPE_RE = re.compile(
    r"\b(diagramas?|diagrams?|flowcharts?|flow[\s-]?charts?|sequence[\s-]?diagrams?|"
    r"class[\s-]?diagrams?|state[\s-]?diagrams?|er[\s-]?diagrams?|"
    r"entity[\s-]?relationship|gantt|mind[\s-]?maps?|mapas?\s+mentales?|"
    r"organigramas?|gr[aá]fic[oa]s?|charts?|graphs?|blueprints?)\b",
    re.IGNORECASE,
)

# "mermaid" por sí solo no basta: pide además un verbo de dibujo.
_MERMAID_RE = re.compile(r"\bmermaid\b", re.IGNORECASE)

# Verbos de "dibujar/renderizar" (dibujo explícito).
_DRAW_VERB_RE = re.compile(
    r"\b(dibuja|dibujar|dib[uú]jame|draw|renderiza|renderizar|render|visualiza|"
    r"visualizar|visual[ií]zame|esboza|esbozar|sketch|graf[ií]ca|graficar|plot|"
    r"plotea|diagramar|diagramame)\b",
    re.IGNORECASE,
)

# Verbos de "hacer/mostrar/generar" (creación de un artefacto).
_MAKE_VERB_RE = re.compile(
    r"\b(hazme|haz|hacer|make|cr[eé]a|crear|create|genera|generar|generate|monta|"
    r"montar|muestra|mu[eé]strame|show|dame|dar|quiero|necesito|puedes|podr[ií]as|"
    r"a[ñn]ade|anade|agrega|add)\b",
    re.IGNORECASE,
)

# Sustantivos de arquitectura de software/sistema.
_ARCH_RE = re.compile(
    r"\b(arquitecturas?|architecture|system[\s-]?design|dise[ñn]o\s+del\s+sistema|"
    r"software[\s-]?design|topolog[ií]as?|topology|blueprints?)\b",
    re.IGNORECASE,
)

# Verbos de "proponer/diseñar" una arquitectura.
_DESIGN_VERB_RE = re.compile(
    r"\b(prop[oó]n|propone|proponer|propuesta|prop[oó]nme|dise[ñn]a|dise[ñn]ar|"
    r"dise[ñn]ame|design|sugiere|sugerir|sugi[eé]reme|plantea|plantear|plante[aá]me|"
    r"propose|architect)\b",
    re.IGNORECASE,
)

# El mensaje ya trae Mermaid: valla ```mermaid o una cabecera al inicio de línea.
_MERMAID_FENCE_RE = re.compile(r"```[ \t]*mermaid\b", re.IGNORECASE)
_MERMAID_HEADER_RE = re.compile(
    r"^[ \t]*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|"
    r"erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart|gitGraph|"
    r"xychart-beta|block-beta|packet-beta|architecture-beta|kanban)\b",
    re.MULTILINE,
)


def has_mermaid_source(text: str) -> bool:
    """True si el texto ya contiene fuente Mermaid (valla o cabecera)."""
    value = str(text or "")
    return bool(_MERMAID_FENCE_RE.search(value) or _MERMAID_HEADER_RE.search(value))


def is_diagram_request(goal: str) -> bool:
    """True si el mensaje pide un diagrama o proponer/diseñar una arquitectura."""
    text = str(goal or "").strip()
    if not text:
        return False
    if has_mermaid_source(text):
        return True
    has_type = bool(_DIAGRAM_TYPE_RE.search(text))
    has_arch = bool(_ARCH_RE.search(text))
    has_draw = bool(_DRAW_VERB_RE.search(text))
    has_make = bool(_MAKE_VERB_RE.search(text))
    has_design = bool(_DESIGN_VERB_RE.search(text))
    # "dibuja/haz un diagrama…"
    if has_type and (has_draw or has_make):
        return True
    # "dibuja con mermaid…"
    if _MERMAID_RE.search(text) and has_draw:
        return True
    # "propón/diseña la arquitectura…" o "dibuja la arquitectura…"
    if has_arch and (has_design or has_draw):
        return True
    return False


def extract_mermaid(text: str) -> str | None:
    """Extrae la fuente Mermaid de ``text`` (valla primero, luego cabecera), o ``None``.

    Con una cabecera a mitad del mensaje se devuelve desde la cabecera hasta el final;
    se asume que lo que sigue es el diagrama.
    """
    value = str(text or "")
    fenced = re.search(
        r"```[ \t]*mermaid[ \t]*\r?\n(.*?)```", value, re.IGNORECASE | re.DOTALL
    )
    if fenced:
        return fenced.group(1).strip()
    header = _MERMAID_HEADER_RE.search(value)
    if header:
        return value[header.start() :].strip()
    return None


__all__ = ["extract_mermaid", "has_mermaid_source", "is_diagram_request"]
