"""Common output-language rule for every prompt that reaches the model.

Internal prompts (planning, agents, curation, review, synthesis, tools) and every
internal artifact (plan steps, node/inter-agent instructions, retries, feedback,
schema and machine fields) are written in English. The only part that follows the
*user's* language is the prose addressed to the user (the final answer, clarification
questions, visible summaries and visible options). This module defines that single,
shared instruction so every call site asks for it the same way.

It lives in the pure ``core`` layer on purpose: it is a leaf constant, importable
from anywhere (agents, orchestration, context) without creating import cycles.
"""

from __future__ import annotations

import re

# Section header: also used to detect whether the rule is already present.
OUTPUT_LANGUAGE_MARKER = "OUTPUT LANGUAGE"

OUTPUT_LANGUAGE_RULE = (
    "# OUTPUT LANGUAGE\n"
    "Everything the system does internally runs in ENGLISH: the planning, the plan "
    "steps you produce, the instructions you give to nodes and to other agents, tool "
    "names/arguments, retry and feedback messages, and every schema or machine field.\n"
    "Speak to the user in the language of the user's latest message: detect it, and if "
    "that message is too short or ambiguous (\"ok\", \"/compact\", a bare symbol or "
    "path), infer it from the recent conversation; only if there is no conversation "
    "either, fall back to English.\n"
    "- USER-FACING PROSE (use the detected language): the final answer, clarification "
    "questions addressed to the user, visible summaries, and visible options/choices.\n"
    "- INTERNAL CONTENT (ALWAYS English, never translate): planning and plan steps, node "
    "and inter-agent instructions, tool calls, retries and feedback, schema field names, "
    "JSON keys, ids, code, file paths, commands and flags.\n"
    "Keep the JSON structure exactly as the declared schema: never rename or translate a "
    "key. When one schema mixes both kinds of field, write only the fields the user reads "
    "in the detected language and leave every internal or machine field in English.\n"
    "Keep code, identifiers, paths, commands and proper names literal and untranslated. "
    "The user's original wording may be quoted as input without translating it."
)


# Display names for the language codes the detector can return. The model already
# gets the common rule; this hint makes the expected answer language explicit even
# when the internal task text is English.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "nl": "Dutch",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
}


def language_name(code: str) -> str:
    """Human-readable name for a language code (``""`` when unknown)."""
    return LANGUAGE_NAMES.get(str(code or "").strip().lower(), "")


def output_language_hint(code: str) -> str:
    """Instruction appended to a user turn: which language the visible answer uses.

    When the language is known, it names it explicitly. When it is unknown (a short
    or ambiguous message, or a language the cheap detector does not cover), it
    delegates to the common rule instead of forcing a default language.
    """
    normalized = str(code or "").strip().lower()
    name = language_name(normalized)
    if name:
        return (
            f"END USER RESPONSE LANGUAGE: {name} ({normalized}). "
            "The task below is an internal instruction and may be in English; do not use "
            "its language to infer the end user's language. Write user-facing prose in the "
            "specified language and preserve the declared JSON schema."
        )
    return (
        "END USER RESPONSE LANGUAGE: match the language of the user's latest message "
        "(detect it; if the message is too short or ambiguous, use the recent "
        "conversation). The task below is an internal instruction and may be in "
        "English; do not use its language to infer the end user's language. Write "
        "user-facing prose in the detected language and preserve the declared JSON "
        "schema."
    )


def with_output_language(text: str) -> str:
    """Return ``text`` with the output-language rule appended (idempotent).

    Idempotent so a prompt assembled twice (e.g. an already-annotated template going
    through ``build_system_prompt`` again) never carries the rule twice.
    """
    if OUTPUT_LANGUAGE_MARKER in text:
        return text
    stripped = text.rstrip()
    return f"{stripped}\n\n{OUTPUT_LANGUAGE_RULE}" if stripped else OUTPUT_LANGUAGE_RULE


# ------------------------------------------------------------------ detección

# Marcas inequívocas del español (signos y letras que el inglés no usa).
_ES_CHARS = re.compile(r"[ñ¿¡áéíóúü]", re.IGNORECASE)
_ES_WORDS = re.compile(
    r"\b(el|la|los|las|un|una|unos|unas|de|del|que|por|para|con|sin|más|pero|"
    r"como|cuando|este|esta|estos|estas|hay|quiero|puedes|ayuda|ayúdame|gracias|"
    r"hola|buenas|buenos|saludos|tardes|noches|dias|crear|arreglar|ejecutar|"
    r"archivo|archivos|carpeta|nodo|agente|agentes|fallo|revisa|agrega|añade)\b",
    re.IGNORECASE,
)
_EN_WORDS = re.compile(
    r"\b(the|a|an|of|to|in|for|with|without|and|but|as|when|this|these|there|"
    r"want|you|help|thanks|hello|hi|hey|hiya|howdy|morning|afternoon|evening|"
    r"create|fix|run|file|files|folder|node|agent|agents|failing|review|add|append)\b",
    re.IGNORECASE,
)

# Ruido que NO debe decidir el idioma: menciones ``@...``, rutas/URLs (tokens con
# separador) y tokens muy largos (identificadores, hashes). Evita que ``.../pytest-of-Ian``
# cuente como el inglés "of".
_NOISE = re.compile(r"@\S+|\S*[\\/]\S*|\S{25,}")


def detect_language(text: str) -> str:
    """Best-effort language of ONE piece of text: ``"es"``, ``"en"`` or ``""``.

    Deterministic and cheap. It never mixes texts and ignores mentions, paths/URLs and
    long identifiers, so a path cannot turn Spanish into English. Spanish-specific
    characters win outright; otherwise the Spanish and English word markers are counted
    and the larger side wins. A tie, no signal or an empty text returns ``""``
    (ambiguous) so the caller can look at the previous user turn.
    """
    cleaned = _NOISE.sub(" ", str(text or ""))
    if not cleaned.strip():
        return ""
    if _ES_CHARS.search(cleaned):
        return "es"
    es = len(_ES_WORDS.findall(cleaned))
    en = len(_EN_WORDS.findall(cleaned))
    if es > en:
        return "es"
    if en > es:
        return "en"
    return ""


def detect_user_language(current: str, history=None) -> str:
    """Language of the user, prioritizing the CURRENT message.

    Order: the current user message wins when it carries a clear signal; otherwise the
    most recent PREVIOUS user turn (never assistant turns); otherwise ``""`` (the caller
    applies the product default). ``history`` is the conversation as ``{"role","content"}``
    dicts.
    """
    language = detect_language(current)
    if language:
        return language
    for turn in reversed(list(history or ())):
        if isinstance(turn, dict) and str(turn.get("role") or "") == "user":
            return detect_language(str(turn.get("content") or ""))
    return ""


# ------------------------------------------------------------------ saludos

# Un saludo SÓLO cuenta si el mensaje entero es un saludo: «hola» sí, «hola,
# arregla el build» no. Determinista y conservador para no confundir una
# cortesía con una tarea. Vocabulario normalizado (minúsculas, sin puntuación).
_GREETING_CORES = frozenset(
    {"hola", "holi", "buenas", "saludos", "hi", "hello", "hey", "hiya", "howdy", "greetings"}
)
_GREETING_PHRASES = frozenset(
    {
        "buenos dias",
        "buenos días",
        "buenas tardes",
        "buenas noches",
        "que tal",
        "qué tal",
        "good morning",
        "good afternoon",
        "good evening",
    }
)
# Vocativos y cortesía que pueden acompañar a un saludo sin convertirlo en otra
# cosa: «hola amigo», «hello there», «hola, ¿cómo estás?», «buenas tardes».
_GREETING_FILLER = frozenset(
    {
        "amigo", "amiga", "chicos", "chicas", "señor", "señora",
        "cómo", "como", "estás", "estas", "qué", "que", "tal",
        "buenos", "dias", "días", "tardes", "noches",
        "there", "friend", "team", "how", "are", "you", "doing", "things",
        "good", "morning", "afternoon", "evening",
    }
)


def _greeting_words(text: str) -> list[str]:
    """Minúsculas, sin puntuación, espacios colapsados: ``"¡Hola!!"`` → ``["hola"]``."""
    lowered = str(text or "").lower()
    return re.sub(r"[^\w\s]", " ", lowered).split()


def is_standalone_greeting(text: str) -> bool:
    """True si TODO ``text`` es un saludo (ES/EN), con o sin puntuación.

    Determinista y conservador: exige que todas las palabras pertenezcan al
    vocabulario de cortesía y que haya al menos un saludo. Un saludo embebido en
    una tarea («hola, arregla el build», «hello can you fix this») devuelve False.
    """
    words = _greeting_words(text)
    if not words:
        return False
    if " ".join(words) in _GREETING_PHRASES:
        return True
    if not any(word in _GREETING_CORES for word in words):
        return False
    return all(word in _GREETING_CORES or word in _GREETING_FILLER for word in words)


# Fallback (sin proveedor o redacción fallida) para el texto de respuesta visible.
# Nunca son etiquetas de UI/CLI: son la prosa de respuesta cuando no hay modelo.
REPLY_FALLBACKS: dict[str, dict[str, str]] = {
    "error": {"es": "No pude completarlo", "en": "I couldn't complete it"},
    "gap": {
        "es": "Me falta una capacidad: {missing}. ¿Quieres que la cree?",
        "en": "I'm missing a capability: {missing}. Do you want me to create it?",
    },
    "options": {"es": "Opciones", "en": "Options"},
    "alternatives": {
        "es": "Te propongo {n} alternativas. Elige una o describe tu propio enfoque.",
        "en": "I propose {n} alternatives. Pick one or describe your own approach.",
    },
    "no_answer": {"es": "(sin respuesta)", "en": "(no answer)"},
    "clarification": {
        "es": "Necesito más información para continuar.",
        "en": "I need more information to continue.",
    },
    "empty_response": {
        "es": "No obtuve respuesta del modelo. Inténtalo de nuevo o reformula tu pregunta.",
        "en": "I didn't get a response from the model. Please try again or rephrase your question.",
    },
    "cancelled": {
        "es": "Turno cancelado",
        "en": "Turn cancelled",
    },
    "greeting": {
        "es": "¡Hola! ¿En qué puedo ayudarte?",
        "en": "Hello! How can I help you?",
    },
}


def reply_fallback(key: str, language: str = "") -> str:
    """Localized fallback response text for ``key`` in ``language`` (English if ``en``)."""
    table = REPLY_FALLBACKS[key]
    return table.get("en" if language == "en" else "es", table["es"])


__all__ = [
    "LANGUAGE_NAMES",
    "OUTPUT_LANGUAGE_MARKER",
    "OUTPUT_LANGUAGE_RULE",
    "REPLY_FALLBACKS",
    "detect_language",
    "detect_user_language",
    "is_standalone_greeting",
    "language_name",
    "output_language_hint",
    "reply_fallback",
    "with_output_language",
]
