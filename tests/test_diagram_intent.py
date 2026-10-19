"""Enrutado explícito de peticiones de diagrama/arquitectura hacia Pele.

Determinista y sin LLM: sólo las peticiones que piden de verdad un diagrama (o
proponer/diseñar una arquitectura) deben disparar la capacidad; el trabajo no
relacionado no debe enrutarse.
"""

from satellite.orchestration.diagram_intent import (
    extract_mermaid,
    has_mermaid_source,
    is_diagram_request,
)


def test_explicit_diagram_requests_invoke() -> None:
    cases = [
        "dibuja un diagrama de la arquitectura del login",
        "hazme un diagrama de flujo para el proceso de pago",
        "create a sequence diagram for the auth flow",
        "muéstrame un gráfico de barras de ventas",
        "add a class diagram of the domain model",
        "necesito un diagrama ER del modelo de datos",
    ]
    for goal in cases:
        assert is_diagram_request(goal) is True, goal


def test_architecture_proposals_invoke() -> None:
    cases = [
        "propón una arquitectura para el servicio de pagos",
        "design the system architecture for a chat app",
        "diseña la arquitectura del sistema de recomendación",
        "sugiere una arquitectura para el backend",
        "¿puedes dibujar la arquitectura del sistema?",
    ]
    for goal in cases:
        assert is_diagram_request(goal) is True, goal


def test_raw_mermaid_source_invokes() -> None:
    assert is_diagram_request("flowchart LR\n A[Mermaid] --> B[SVG]") is True
    assert is_diagram_request("sequenceDiagram\n A->>B: Hi") is True
    assert is_diagram_request("```mermaid\nsequenceDiagram\n A->>B: Hi\n```") is True


def test_unrelated_work_does_not_invoke() -> None:
    cases = [
        "arregla el bug del login",
        "crea un archivo README",
        "explica la arquitectura del proyecto",
        "documenta la arquitectura actual",
        "resume el proyecto",
        "¿qué herramientas tienes?",
        "genera el esquema de la base de datos",
        "instala las dependencias",
        "revisa el diagrama de arquitectura y dime si está mal",
    ]
    for goal in cases:
        assert is_diagram_request(goal) is False, goal


def test_extract_mermaid_prefers_fence() -> None:
    text = "Aquí tienes:\n```mermaid\nflowchart LR\n A --> B\n```\ngracias"
    assert extract_mermaid(text) == "flowchart LR\n A --> B"


def test_extract_mermaid_from_header() -> None:
    assert extract_mermaid("flowchart TD\n A --> B") == "flowchart TD\n A --> B"
    assert extract_mermaid("dibújame esto:\ngraph LR\n A --> B") == "graph LR\n A --> B"


def test_extract_mermaid_none_without_source() -> None:
    assert extract_mermaid("dibuja un diagrama del login") is None
    assert has_mermaid_source("dibuja un diagrama del login") is False
