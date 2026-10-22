"""Tests del clasificador de consultas (chat-directo vs tarea)."""

from pathlib import Path

from satellite.core.task import Task
from satellite.orchestration.chat_detector import (
    has_project_context,
    has_task_hint,
    heuristic_is_chat,
    is_chat_goal,
    is_desktop_action,
    is_do_it_request,
    is_web_research_request,
)
from satellite.providers.fake import FakeLLM


def make_task(goal: str, root: Path) -> Task:
    """Tarea con una raíz real (temporal): nada depende del repo del usuario."""
    return Task(goal=goal, project_root=root)


def test_heuristic_basic_greeting_is_chat() -> None:
    assert heuristic_is_chat("hola, ¿cómo estás?") is True
    assert heuristic_is_chat("¿qué es la fotosíntesis?") is True


def test_heuristic_task_hint_not_chat() -> None:
    assert heuristic_is_chat("crea una página html que diga hola") is False
    assert heuristic_is_chat("arregla el error en main.py") is False
    assert heuristic_is_chat("implementa una función que sume") is False


def test_desktop_actions_are_tasks_not_chat() -> None:
    """Abrir/controlar Safari es trabajo (tools desktop_*), no charla."""
    for text in (
        "Puedes abrir safari y entrar a google docs",
        "abre Google Chrome y ve a docs.google.com",
        "haz clic en la ventana de Safari",
        "open Safari and navigate to Google Docs",
    ):
        assert is_desktop_action(text) is True
        assert heuristic_is_chat(text) is False
    # Preguntar por el navegador no es una acción de escritorio.
    assert is_desktop_action("¿qué es Safari?") is False
    assert heuristic_is_chat("¿qué es Safari?") is True


def test_desktop_action_requires_an_explicit_app_or_window() -> None:
    """No confundir control de escritorio con buscar en internet o correr comandos."""
    # Peticiones de escritorio: app/ventana nombrada + verbo.
    assert is_desktop_action("abre una nueva terminal") is True
    assert is_desktop_action("abre la app de música") is True
    assert is_desktop_action("abre Music") is True
    assert is_desktop_action("busca vuelos en Google Chrome") is True
    assert is_desktop_action("haz scroll en youtube") is True
    # Sin app/ventana nombrada: no es control de escritorio.
    assert is_desktop_action("busca esto en internet") is False
    assert is_desktop_action("busca esto en google") is False
    assert is_desktop_action("corre este comando") is False
    assert is_desktop_action("ejecuta este comando en la terminal") is False
    assert is_desktop_action("abre el archivo main.py") is False


def test_estas_checking_is_not_chat(tmp_path: Path) -> None:
    """'estás chequeando' es una queja de trabajo, no un saludo."""
    text = (
        "Entonces porque no lo haces y solo estas cheque y cheque con el readme, "
        "el readme de que sirve? Hazlo y olvida el readme"
    )
    assert heuristic_is_chat(text) is False
    assert has_project_context(text) is True
    assert is_do_it_request(text) is True

    provider = FakeLLM(default_content="chat")
    assert is_chat_goal(make_task(text, tmp_path), provider) is False
    assert provider.requests == []
    assert is_do_it_request("Hazlo") is True
    assert is_do_it_request("¿Qué es un closure?") is False


def test_web_research_requests_are_tasks_not_chat() -> None:
    """Una búsqueda explícita en internet debe llegar a web_researcher."""
    for text in (
        "Puedes investigar en internet si se ha intentado algo igual?",
        "Busca en internet si alguien ha hecho un GPT pequeño en una Basys 3",
        "Investigame si se ha intentado algo igual en internet",
        "Consulta en la web los proyectos parecidos",
        "Research online whether this has been done before",
        "Search the web for tiny language models on FPGAs",
    ):
        assert is_web_research_request(text) is True, text
        assert heuristic_is_chat(text) is False, text

    # Conocimiento general o búsqueda local no son investigación web.
    assert is_web_research_request("¿qué es internet?") is False
    assert heuristic_is_chat("¿qué es internet?") is True
    assert is_web_research_request("busca el archivo en el proyecto") is False
    assert is_web_research_request("corre este comando") is False


def test_web_research_short_question_does_not_call_the_llm_classifier(
    tmp_path: Path,
) -> None:
    provider = FakeLLM(default_content="chat")
    task = make_task(
        "Puedes investigar en internet si se ha intentado algo igual?", tmp_path
    )
    assert is_chat_goal(task, provider) is False
    assert provider.requests == []


def test_heuristic_long_not_chat() -> None:
    assert heuristic_is_chat("x" * 200) is False


def test_heuristic_question_short_is_chat() -> None:
    assert heuristic_is_chat("¿Cuál es la capital de Francia?") is True


def test_heuristic_meta_questions_about_the_system_are_chat() -> None:
    """"¿Qué puedes hacer?" se planificaba por el verbo "hacer" (bug real)."""
    assert heuristic_is_chat("¿Qué puedes hacer?") is True
    assert heuristic_is_chat("¿Qué agentes tienes disponibles?") is True
    assert heuristic_is_chat("¿Qué skills hay guardadas?") is True
    assert heuristic_is_chat("¿Qué herramientas tienes?") is True
    assert heuristic_is_chat("¿Qué modelo eres?") is True
    assert heuristic_is_chat("¿Quién eres?") is True


def test_heuristic_ignores_trailing_whitespace() -> None:
    assert heuristic_is_chat("¿Qué agentes tienes?   ") is True
    assert heuristic_is_chat("\n ¿Puedes ayudarme? ") is True


def test_task_hint_needs_action_plus_project_context() -> None:
    # acción sin contexto de proyecto → no es tarea
    assert has_task_hint("haz lo que puedas") is False
    # acción + contexto → tarea
    assert has_task_hint("crea un archivo") is True
    assert has_task_hint("actualiza el README del proyecto") is True
    assert has_task_hint("crea un agente que resuma documentos") is True
    assert has_task_hint("arregla el fallo del script de python") is True


def test_heuristic_keeps_real_tasks_as_tasks() -> None:
    assert heuristic_is_chat("crea una página html que diga hola") is False
    assert heuristic_is_chat("crea un archivo con hola") is False
    assert heuristic_is_chat("escribe tests para el módulo de store") is False
    assert heuristic_is_chat("¿puedes crear un script que sume?") is False


# --- casos límite (bloque 1 del catálogo de comportamientos raros) ---


def test_help_requests_and_filler_are_chat() -> None:
    """"ayúdame" y el relleno no deben acabar en un grafo."""
    for text in (
        "ayúdame con esto",
        "ayudame",
        "ok",
        "vale",
        "perfecto",
        "entendido",
        "gracias!",
        "me ayudas?",
    ):
        assert heuristic_is_chat(text) is True, text


def test_long_conversational_message_is_still_chat() -> None:
    """El corte por longitud ya no se aplica antes de mirar las pistas."""
    largo = "Te pego un texto para que me des tu opinión: " + "bla " * 60 + "¿qué opinas?"
    assert len(largo) > 160
    assert heuristic_is_chat(largo) is True
    # …pero un mensaje largo sin ninguna señal sigue siendo tarea
    assert heuristic_is_chat("x" * 200) is False


def test_diagnostic_question_about_the_project_is_a_task() -> None:
    """"¿por qué falla mi script?" necesita mirar el proyecto, no charlar."""
    assert has_task_hint("¿por qué falla mi script?") is True
    assert heuristic_is_chat("¿por qué falla mi script?") is False
    assert heuristic_is_chat("el test de store está fallando") is False
    # el mismo verbo en una pregunta de conocimiento general sigue siendo chat
    assert heuristic_is_chat("¿qué es un script en python?") is True
    assert heuristic_is_chat("explícame la diferencia entre error y excepción") is True


def test_context_detected_by_file_extension() -> None:
    assert has_project_context("revisa main.py") is True
    assert has_task_hint("arregla el fallo en main.py") is True
    assert heuristic_is_chat("arregla el fallo en main.py") is False


def test_english_questions_are_classified() -> None:
    assert heuristic_is_chat("what can you do?") is True
    assert heuristic_is_chat("help me with this") is True
    assert heuristic_is_chat("what is a closure?") is True
    assert heuristic_is_chat("create a file with hello") is False
    assert heuristic_is_chat("fix the failing test in store.py") is False


def test_is_chat_goal_trusts_heuristic_without_llm(tmp_path: Path):
    """Chat claro (heurística) → NO consulta al LLM (modelos pequeños no fiables)."""

    class FakeChatLLM(FakeLLM):
        async def complete(self, request):
            from satellite.core.messages import LLMResponse

            self.requests.append(request)
            return LLMResponse(content="task")  # aunque el LLM diga task, la heurística manda

    provider = FakeChatLLM()
    task = make_task("¿qué es el infinitivo?", tmp_path)
    assert is_chat_goal(task, provider) is True
    # no se llamó al LLM (heurística decidió sola)
    assert len(provider.requests) == 0


def test_is_chat_goal_ambiguous_uses_llm(tmp_path: Path) -> None:
    """Consulta corta sin pistas claras (ni chat ni tarea) → consulta al LLM."""
    provider = FakeLLM(script=[{"content": "chat"}])
    # "clima de bogota" no tiene pistas de chat ni de tarea, no termina en ? → LLM decide
    task = make_task("clima de bogota", tmp_path)
    assert is_chat_goal(task, provider) is True
    assert len(provider.requests) == 1


def test_is_chat_goal_task_hint_false_even_with_provider(tmp_path: Path) -> None:
    provider = FakeLLM(script=[{"content": "chat"}])
    task = make_task("crea un archivo", tmp_path)
    assert is_chat_goal(task, provider) is False
    # no debe consultar al LLM (la pista de tarea es determinante)
    assert len(provider.requests) == 0


def test_is_chat_goal_without_provider_falls_to_heuristic(tmp_path: Path) -> None:
    task = make_task("hola, ¿qué tal?", tmp_path)
    assert is_chat_goal(task, None) is True
    task2 = make_task("escribe una función en python", tmp_path)
    assert is_chat_goal(task2, None) is False
