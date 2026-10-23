"""Política de idioma del producto: prompts internos en inglés y respuesta al usuario.

Regla de producto que estas pruebas protegen:

1. **Todo prompt interno va en inglés** (piloto, agentes, curador, revisión,
   herramientas y mensajes internos), o lleva la instrucción común de idioma.
2. La **respuesta visible** sigue el idioma del mensaje/conversación del usuario:
   eso lo garantiza la instrucción común, inyectada en cada prompt que llega al
   modelo.
3. La **interfaz/CLI visible no se deslocaliza**: sigue en español.
4. Los **schemas y los prompts concuerdan** (no repetir el fallo de `code_explainer`,
   cuyo prompt pedía claves que su `output_schema` no declaraba).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from satellite.agents.base import AgentContext
from satellite.agents.prompting import build_meta_messages
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.runtime import LLMAgent, build_system_prompt
from satellite.core.language import (
    OUTPUT_LANGUAGE_MARKER,
    OUTPUT_LANGUAGE_RULE,
    detect_language,
    detect_user_language,
    is_standalone_greeting,
    reply_fallback,
    with_output_language,
)
from satellite.agents.schemas import CURATOR_OUTPUT_SCHEMA, PROPOSED_AGENT_SCHEMA
from satellite.context.compaction import summarize_turns
from satellite.core.spec import AgentSpec
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.chat import Turn, reply_from_result
from satellite.orchestration.pilot import PilotOrchestrator
from satellite.providers.fake import FakeLLM

PKG = Path(__file__).resolve().parent.parent / "satellite" / "agents"
PROMPTS = PKG / "prompts"
EXAMPLES = PKG / "examples"
BASE_PROMPT_IDS = (
    "pilot",
    "agent_creator",
    "skill_curator",
    "web_researcher",
) + tuple(
    f"{language}_{role}"
    for language in (
        "python",
        "javascript",
        "typescript",
        "java",
        "csharp",
        "cpp",
        "sql",
        "go",
        "php",
        "rust",
    )
    for role in ("coder", "auditor")
)

# Palabras inequívocamente españolas que aparecerían en un prompt escrito en
# español. Los prompts en inglés usan sus equivalentes (ROLE, Task, Context,
# Tools, Output, Inputs, Catalog...), así que no hay falsos positivos.
_SPANISH_PROMPT = re.compile(
    r"\b(Eres|Responde|Devuelve|Tarea|Necesidad|Contexto|Herramientas|Prohibiciones|"
    r"Reglas|Entradas|Catálogo|Debes|Nunca|Usa|SOLO)\b"
)


def run(coro):
    return asyncio.run(coro)


def _base_prompts() -> DefaultPromptRegistry:
    registry = DefaultPromptRegistry()
    registry.load_dir(PROMPTS)
    return registry


class _PilotFactory:
    """Crea el piloto real (prompts del paquete) sin tools, hablando con ``provider``."""

    def __init__(self, provider: FakeLLM) -> None:
        self.provider = provider

    def create(self, spec_id: str, *, model_role: str | None = None) -> LLMAgent:
        prompts = _base_prompts()
        return LLMAgent(
            spec=AgentSpec(id=spec_id, name="Pilot", capabilities=("converse",), prompt_ref=spec_id),
            system_prompt=prompts.get_system(spec_id),
            user_template=prompts.get_user(spec_id),
            tools=[],
            provider=self.provider,
        )


def _pilot(provider: FakeLLM) -> PilotOrchestrator:
    services = RuntimeServices(registry=DefaultAgentRegistry(), factory=_PilotFactory(provider))
    return PilotOrchestrator(services)


# ---------------------------------------------------------------- (1) prompts en inglés


@pytest.mark.parametrize("prompt_id", BASE_PROMPT_IDS)
def test_base_prompts_are_written_in_english(prompt_id: str) -> None:
    registry = _base_prompts()
    system = registry.get_system(prompt_id)
    user = registry.get_user(prompt_id)
    assert system.strip() and user.strip()
    assert "# ROLE" in system
    combined = system + "\n" + user
    assert _SPANISH_PROMPT.search(combined) is None, combined


@pytest.mark.parametrize(
    "name",
    (
        "code_analyzer.txt",
        "code_explainer.txt",
        "html_reviewer.txt",
        "html_writer.txt",
        "test_runner.txt",
    ),
)
def test_example_prompt_templates_are_in_english(name: str) -> None:
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    assert _SPANISH_PROMPT.search(text) is None, text


def test_desktop_operator_prompt_is_in_english() -> None:
    system = (PROMPTS / "desktop_operator.system.txt").read_text(encoding="utf-8")
    assert _SPANISH_PROMPT.search(system) is None, system
    assert "UNTRUSTED" in system


def test_preprocessor_prompts_are_in_english(tmp_path: Path) -> None:
    """Los prompts internos sin respuesta al usuario también van en inglés."""
    from satellite.context.preprocessor import LocalPreprocessor

    proc_provider = FakeLLM(script=[{"content": "{}"}])
    run(LocalPreprocessor(proc_provider, tmp_path).preprocess("haz la app"))
    sent = "\n".join(m.content for m in proc_provider.requests[-1].messages)
    assert _SPANISH_PROMPT.search(sent) is None, sent


# ---------------------------------------------------------------- instrucción común


def test_build_system_prompt_carries_the_common_language_rule() -> None:
    system = build_system_prompt("# ROLE\nBe helpful.")
    assert OUTPUT_LANGUAGE_MARKER in system
    assert "language of the user's latest message" in system


def test_output_language_rule_is_idempotent() -> None:
    once = with_output_language("# ROLE\nBe helpful.")
    twice = with_output_language(once)
    assert twice.count(OUTPUT_LANGUAGE_MARKER) == 1
    assert once == twice


def test_common_rule_detects_language_and_keeps_literals() -> None:
    rule = OUTPUT_LANGUAGE_RULE.lower()
    assert "language of the user's latest message" in rule  # detecta el idioma
    assert "recent conversation" in rule  # usa el contexto si es ambiguo
    assert "english" in rule  # último recurso explícito
    assert "literal" in rule  # nombres/código/paths se mantienen literales


def test_common_rule_keeps_internal_process_in_english() -> None:
    """Lo interno va SIEMPRE en inglés; sólo la prosa visible sigue el idioma del usuario."""
    lower = OUTPUT_LANGUAGE_RULE.lower()
    # prosa visible → idioma detectado
    assert "user-facing prose" in lower
    assert "detected language" in lower
    assert "final answer" in lower
    assert "summaries" in lower and "options" in lower
    assert "question" in lower  # preguntas de aclaración visibles
    # interno → siempre inglés, incluidos los pasos de plan y las instrucciones
    assert "internal content" in lower
    assert "always english" in lower
    assert "plan steps" in lower
    assert "inter-agent instructions" in lower
    assert "retries and feedback" in lower
    # el paso de plan NO puede quedar como prosa del usuario (el bug a corregir)
    assert "plan step instructions" not in lower
    # estructura JSON: no renombrar claves
    assert "never rename or translate a key" in lower


@pytest.mark.parametrize("prompt_id", BASE_PROMPT_IDS)
def test_meta_prompts_reach_the_model_with_the_language_rule(prompt_id: str) -> None:
    """Creator/curator (y cualquier prompt base) vía build_meta_messages → llevan la regla."""
    registry = _base_prompts()
    messages, _stable = build_meta_messages(
        system_prompt=registry.get_system(prompt_id),
        user_template=registry.get_user(prompt_id),
        output_schema={"type": "object"},
    )
    assert OUTPUT_LANGUAGE_MARKER in messages[0].content


def test_executing_agent_sends_the_language_rule(tmp_path: Path) -> None:
    spec = AgentSpec(
        id="html_writer",
        name="HTML Writer",
        capabilities=("write_html",),
        prompt_ref="html_writer",
        tools=(),
    )
    provider = FakeLLM(script=[{"content": "listo"}])
    agent = LLMAgent(
        spec=spec,
        system_prompt="# ROLE\nWrite HTML.",
        user_template="Task: {task}\nContext: {context}\nFeedback: {feedback}",
        tools=[],
        provider=provider,
        project_root=tmp_path,
    )
    run(agent.execute({"node_id": "n1"}, AgentContext()))
    system = provider.requests[0].messages[0].content
    assert OUTPUT_LANGUAGE_MARKER in system


# ---------------------------------------------------------------- (2) respuesta en el idioma del usuario


def test_pilot_reply_asks_for_the_user_language(tmp_path: Path) -> None:
    provider = FakeLLM(script=[{"content": "Hola, ¿en qué te ayudo?"}])
    result = run(_pilot(provider).execute(Task(goal="hola", project_root=tmp_path)))
    assert result.output["mode"] == "pilot_reply"
    system = provider.requests[-1].messages[0].content
    assert OUTPUT_LANGUAGE_MARKER in system
    assert "language of the user's latest message" in system


def test_pilot_empty_completion_is_a_helpful_localized_fallback(tmp_path: Path) -> None:
    """Una completion vacía que NO es un saludo no se muestra como «(sin respuesta)»:
    avisa de forma útil y en el idioma del usuario."""
    provider = FakeLLM(script=[{"content": ""}])
    result = run(
        _pilot(provider).execute(Task(goal="¿qué es la fotosíntesis?", project_root=tmp_path))
    )
    assert result.output["mode"] == "pilot_reply"
    assert result.summary.strip()
    assert "(sin respuesta)" not in result.summary
    assert "no obtuve respuesta" in result.summary.lower()
    assert result.output["user_language"] == "es"


def test_pilot_empty_completion_is_localized_in_english(tmp_path: Path) -> None:
    provider = FakeLLM(script=[{"content": "   "}])
    result = run(_pilot(provider).execute(Task(goal="what is a closure?", project_root=tmp_path)))
    assert "(no answer)" not in result.summary
    assert result.summary.strip()
    assert "didn't get a response" in result.summary.lower()
    assert result.output["user_language"] == "en"


def test_pilot_empty_greeting_greets_back_in_spanish(tmp_path: Path) -> None:
    """El caso reportado: «Hola» con completion vacía debe saludar, no avisar de un fallo."""
    provider = FakeLLM(script=[{"content": ""}])
    result = run(_pilot(provider).execute(Task(goal="Hola", project_root=tmp_path)))
    assert result.summary == "¡Hola! ¿En qué puedo ayudarte?"
    assert result.output["user_language"] == "es"


def test_pilot_empty_greeting_greets_back_in_english(tmp_path: Path) -> None:
    provider = FakeLLM(script=[{"content": ""}])
    result = run(_pilot(provider).execute(Task(goal="Hello", project_root=tmp_path)))
    assert result.summary == "Hello! How can I help you?"
    assert result.output["user_language"] == "en"


def test_pilot_greeting_embedded_in_a_task_does_not_greet(tmp_path: Path) -> None:
    """Un saludo dentro de una tarea real no debe activar la respuesta de saludo."""
    provider = FakeLLM(script=[{"content": "   "}])
    result = run(
        _pilot(provider).execute(Task(goal="hola, arregla el build", project_root=tmp_path))
    )
    assert "no obtuve respuesta" in result.summary.lower()


def test_compaction_summary_prompt_carries_the_language_rule() -> None:
    """La síntesis de la compactación también lleva la regla común de idioma."""
    provider = FakeLLM(script=[{"content": "resumen"}])
    run(summarize_turns([Turn("user", "hola, ¿qué haces?")], provider))
    system = provider.requests[-1].messages[0].content
    assert OUTPUT_LANGUAGE_MARKER in system


def test_capability_gap_reply_follows_user_language() -> None:
    """El gap se localiza (ya no es texto fijo en español) y la prosa gana al fallback."""

    def gap(language: str) -> TaskResult:
        return TaskResult(
            task_id="t",
            status=TaskStatus.NEEDS_USER_INPUT,
            output={
                "mode": "capability_gap",
                "capability_gap": {"missing": "audit the project"},
                "options": ["create an agent for it"],
                "user_language": language,
            },
        )

    es = reply_from_result(gap("es"))
    assert "Me falta una capacidad" in es and "Opciones:" in es
    en = reply_from_result(gap("en"))
    assert "I'm missing a capability" in en and "Options:" in en
    # el literal (la capacidad que falta) no se traduce ni se pierde
    assert "audit the project" in es and "audit the project" in en

    # si el resultado ya trae prosa localizada, se prefiere al fallback
    localized = TaskResult(
        task_id="t",
        status=TaskStatus.NEEDS_USER_INPUT,
        output={
            "mode": "capability_gap",
            "capability_gap": {"missing": "audit the project"},
            "user_language": "en",
            "content": "I need a new capability to audit the project. Want me to build it?",
        },
    )
    assert reply_from_result(localized) == (
        "I need a new capability to audit the project. Want me to build it?"
    )


def test_error_reply_follows_user_language_and_keeps_technical_detail() -> None:
    def failed(language: str, content: str = "") -> TaskResult:
        output: dict = {"user_language": language}
        if content:
            output["content"] = content
        return TaskResult(
            task_id="t",
            status=TaskStatus.FAILED,
            error="exit code 2: no such file /tmp/x.py",
            output=output,
        )

    en = reply_from_result(failed("en"))
    assert en.startswith("I couldn't complete it")
    assert "exit code 2: no such file /tmp/x.py" in en  # diagnóstico literal intacto
    es = reply_from_result(failed("es"))
    assert es.startswith("No pude completarlo")
    assert "exit code 2: no such file /tmp/x.py" in es
    # prosa localizada disponible → gana
    assert reply_from_result(
        failed("en", "The build failed on a missing file.")
    ) == "The build failed on a missing file."


def test_cli_tool_listing_stays_in_spanish(tmp_path: Path, monkeypatch) -> None:
    """El catálogo de herramientas del CLI no se traduce (es interfaz visible)."""
    from satellite.cli.app import app
    from typer.testing import CliRunner

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    result = CliRunner().invoke(app, ["tools", "list"])
    assert result.exit_code == 0, result.output
    assert "herramientas del kernel" in result.output


# ---------------------------------------------------------------- (4) schema ⇄ prompt


def test_curator_schema_matches_its_prompt() -> None:
    prompt = _base_prompts().get_system("skill_curator")
    ops = CURATOR_OUTPUT_SCHEMA["properties"]["operations"]["items"]["properties"]["op"]["enum"]
    for op in ops:
        assert op in prompt, op


def test_agent_creator_schema_matches_its_prompt() -> None:
    prompt = _base_prompts().get_system("agent_creator")
    for kind in PROPOSED_AGENT_SCHEMA["properties"]["type"]["enum"]:
        assert kind in prompt, kind
    for field in (
        "system_prompt",
        "user_template",
        "output_schema",
        "example_tasks",
        "can_call",
    ):
        assert field in prompt, field


def test_desktop_operator_prompt_and_schema_agree() -> None:
    registry = DefaultAgentRegistry()
    registry.load_dir(PKG / "specs")
    schema = registry.get("desktop_operator").output_schema or {}
    prompt = (PROMPTS / "desktop_operator.system.txt").read_text(encoding="utf-8")
    assert schema.get("required")
    for key in schema["required"]:
        assert key in prompt, key


def test_agent_creator_asks_for_prompt_schema_coherence() -> None:
    """El creador debe exigir que el prompt y el output_schema usen los mismos nombres."""
    prompt = _base_prompts().get_system("agent_creator")
    assert "output_schema" in prompt
    assert "IDENTICAL" in prompt


def _example_specs_with_schema() -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for path in sorted(EXAMPLES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("output_schema"), dict):
            found.append((str(data.get("prompt_ref") or data.get("id") or path.stem), data))
    return found


def test_example_prompt_and_schema_keys_agree() -> None:
    """Un prompt no puede pedir claves que su output_schema no declare.

    Es el fallo del `code_explainer`: el prompt pedía secciones con otros nombres
    (y en otro idioma) que el esquema, así que una respuesta correcta parecía vacía.
    """
    cases = _example_specs_with_schema()
    assert cases, "se esperaba al menos un ejemplo con output_schema (code_explainer)"
    for prompt_id, spec in cases:
        prompt = (EXAMPLES / f"{prompt_id}.txt").read_text(encoding="utf-8")
        schema = spec["output_schema"]
        for key in schema.get("properties", {}):
            assert key in prompt, (prompt_id, key)
        for key in schema.get("required", []):
            assert key in schema["properties"], (prompt_id, key)


def test_code_explainer_example_is_aligned() -> None:
    spec = json.loads((EXAMPLES / "code_explainer.json").read_text(encoding="utf-8"))
    prompt = (EXAMPLES / "code_explainer.txt").read_text(encoding="utf-8")
    schema = spec["output_schema"]
    assert schema["required"] == ["summary"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"summary", "behavior"}
    # el prompt nombra EXACTAMENTE las claves del esquema (sin guiones en español)
    assert "summary" in prompt and "behavior" in prompt
    assert "resumen" not in prompt and "comportamiento" not in prompt


# ---------------------------------------------------------------- (5) campos internos vs visibles


def test_pilot_marks_delegation_internal_and_reply_user_facing() -> None:
    """La tarea delegada es instrucción interna (inglés); sólo la respuesta es del usuario."""
    prompt = _base_prompts().get_system("pilot")
    assert "Write it in English" in prompt
    assert "in the user's language" in prompt


def test_preprocessor_marks_internal_fields_english(tmp_path: Path) -> None:
    from satellite.context.preprocessor import LocalPreprocessor

    provider = FakeLLM(script=[{"content": "{}"}])
    run(LocalPreprocessor(provider, tmp_path).preprocess("haz la app"))
    sent = "\n".join(m.content for m in provider.requests[-1].messages)
    assert OUTPUT_LANGUAGE_MARKER in sent
    # refined_prompt y missing_info son internos → inglés
    assert "refined_prompt" in sent and "INTERNAL" in sent and "English" in sent
    # user_prompt es lo único visible → idioma del usuario
    assert "user_prompt" in sent and "user's language" in sent


def test_preprocessor_prompt_has_no_refined_prompt_contradiction(tmp_path: Path) -> None:
    """`refined_prompt` es un objetivo de trabajo SIEMPRE en inglés, aunque el goal sea claro.

    Antes la instrucción decía «si está claro, return refined_prompt = the same goal»,
    que para una entrada en español dejaba el objetivo interno en español: contradictorio
    con un campo interno en inglés.
    """
    from satellite.context.preprocessor import LocalPreprocessor

    provider = FakeLLM(script=[{"content": "{}"}])
    run(LocalPreprocessor(provider, tmp_path).preprocess("explícame el módulo"))
    sent = "\n".join(m.content for m in provider.requests[-1].messages)
    # el mensaje original viaja como dato, sin reescribirlo
    assert "explícame el módulo" in sent
    # ...pero refined_prompt nunca se pide «igual que el goal»
    assert "the same goal" not in sent
    assert "ALWAYS in English" in sent
    assert "never echo a non-English" in sent
    # missing_info también es interno inglés
    assert "missing_info" in sent and "in English (internal)" in sent


def test_preprocessor_reformulates_spanish_goal_into_english(tmp_path: Path) -> None:
    """Con la infra fake: el preprocessor usa el refined_prompt inglés del modelo y
    conserva la pregunta de aclaración en el idioma del usuario."""
    from satellite.context.preprocessor import LocalPreprocessor

    class FakeRefiner(FakeLLM):
        async def complete(self, request):
            from satellite.core.messages import LLMResponse

            self.requests.append(request)
            return LLMResponse(
                content=json.dumps(
                    {
                        "refined_prompt": "Explain the module's behavior",
                        "needs_user_input": True,
                        "user_prompt": "¿Qué módulo quieres?",
                        "missing_info": ["target module"],
                        "needs": {"files_needed": [], "symbols_needed": []},
                    }
                )
            )

    provider = FakeRefiner()
    result = run(LocalPreprocessor(provider, tmp_path).preprocess("explícame el módulo"))

    assert result["refined_prompt"] == "Explain the module's behavior"  # interno, inglés
    assert result["refined_prompt"] != "explícame el módulo"  # no es el goal crudo
    assert result["user_prompt"] == "¿Qué módulo quieres?"  # visible, idioma del usuario
    assert result["missing_info"] == ["target module"]
    sent = "\n".join(m.content for m in provider.requests[-1].messages)
    assert "explícame el módulo" in sent  # el original se conserva como entrada/dato


# ---------------------------------------------------------------- (6) salidas visibles localizadas


def test_detect_language_basic() -> None:
    assert detect_language("¿qué hace este archivo?") == "es"
    assert detect_language("crear un archivo de configuración") == "es"
    assert detect_language("what does this file do?") == "en"
    assert detect_language("1234 ...") == ""
    assert detect_language("") == ""


@pytest.mark.parametrize(
    "text",
    (
        "Hola",
        "HOLA!",
        "  hola  ",
        "hola, ¿cómo estás?",
        "buenos días",
        "buenas tardes",
        "hello",
        "Hello!",
        "hi",
        "hey there",
        "good morning",
    ),
)
def test_standalone_greeting_detects_punctuation_and_case(text: str) -> None:
    assert is_standalone_greeting(text) is True, text


@pytest.mark.parametrize(
    "text",
    (
        "",
        "   ",
        "hola, arregla el build",
        "hello can you help me fix this bug",
        "fix the build",
        "¿qué hora es?",
        "gracias por todo, necesito ayuda",
    ),
)
def test_standalone_greeting_rejects_embedded_or_non_greeting(text: str) -> None:
    assert is_standalone_greeting(text) is False, text


def test_reply_fallback_is_localized() -> None:
    assert reply_fallback("error", "es").startswith("No pude completarlo")
    assert reply_fallback("error", "en").startswith("I couldn't complete it")
    assert "capacidad" in reply_fallback("gap", "es")
    assert "capability" in reply_fallback("gap", "en")
    assert reply_fallback("greeting", "es") == "¡Hola! ¿En qué puedo ayudarte?"
    assert reply_fallback("greeting", "en") == "Hello! How can I help you?"


def test_current_english_beats_spanish_history() -> None:
    """El mensaje actual manda: un inglés actual no se vuelve español por el historial."""
    history = [
        {"role": "user", "content": "arregla el archivo de configuración"},
        {"role": "assistant", "content": "¿Quieres que lo revise?"},
    ]
    assert detect_user_language("fix the failing build", history) == "en"


def test_current_spanish_beats_english_history() -> None:
    history = [
        {"role": "user", "content": "explain this file"},
        {"role": "assistant", "content": "It reads the config."},
    ]
    assert detect_user_language("¿qué hace este archivo?", history) == "es"


def test_ambiguous_current_uses_latest_user_turn_and_ignores_assistant() -> None:
    """Mensaje actual ambiguo → el turno de USUARIO más reciente; el del asistente no cuenta."""
    history = [
        {"role": "user", "content": "explícame el módulo"},
        {"role": "assistant", "content": "Sure, here is the explanation of the module."},
    ]
    assert detect_user_language("ok", history) == "es"

    # si el último turno de usuario es inglés, gana ese (aunque el asistente sea español)
    history_en = [
        {"role": "user", "content": "explain the module"},
        {"role": "assistant", "content": "Claro, aquí tienes la explicación."},
    ]
    assert detect_user_language("ok", history_en) == "en"

    # sin turno de usuario claro → "" (decide el fallback de producto)
    assert detect_user_language("ok", [{"role": "assistant", "content": "Esto es una respuesta."}]) == ""


def test_preprocessed_goal_uses_original_language(tmp_path: Path) -> None:
    """Si el preprocessor reescribe el goal a inglés, se detecta el original (refined_from)."""
    provider = FakeLLM(script=[{"content": "ok"}])
    task = Task(
        goal="Explain the module's behavior",
        project_root=tmp_path,
        metadata={"refined_from": "explícame el módulo"},
    )
    result = run(_pilot(provider).execute(task))
    assert result.output["user_language"] == "es"  # el original manda, no el goal refinado
    assert "Spanish" in provider.requests[-1].messages[-1].content


def test_language_hint_stores_only_the_code(tmp_path: Path) -> None:
    """El hint guarda el código de idioma, nunca el mensaje (no se persiste contenido)."""
    result = run(
        _pilot(FakeLLM(script=[{"content": "ok"}])).execute(
            Task(goal="arregla el archivo de configuración", project_root=tmp_path)
        )
    )
    assert result.output["user_language"] == "es"
    assert "configuración" not in str(result.output)

    english = run(
        _pilot(FakeLLM(script=[{"content": "ok"}])).execute(
            Task(goal="fix the failing build", project_root=tmp_path)
        )
    )
    assert english.output["user_language"] == "en"
