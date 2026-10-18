"""Capacidades por modelo: qué parámetros acepta cada familia.

Los modelos de razonamiento de OpenAI (series ``o`` y ``gpt-5``) **rechazan**
``max_tokens`` —exigen ``max_completion_tokens``— y un ``temperature`` distinto
del de defecto: la llamada falla con HTTP 400. La tabla es por prefijo de id y
se puede sobrescribir desde config (``llm.models.<id>``) para no quedar atados a
ella cuando salga una familia nueva.

Esfuerzo de razonamiento: el **control** (qué parámetro lleva el nivel) es lo que
cambia entre proveedores y aquí se nombra explícitamente:

- ``reasoning_effort``          → OpenAI-compatible (DeepSeek, xAI, OpenRouter…).
- ``thinking_level``            → Gemini en su API **nativa** (no se envía por la
                                  forma OpenAI-compatible: ver ``model_sources``).
- ``output_config.effort``      → variantes que anidan el nivel.
- ``budget_tokens``             → Anthropic: ``thinking.budget_tokens``.
- ``provider-specific``         → cualquier otro canal declarado a mano.

Los niveles salen de la tabla de familias, de ``llm.models.<id>`` o de lo que anuncie
el proveedor en su listado de modelos; **nunca se inventan**. Cuando sólo hay tabla por
prefijo, la metadata se marca como ``inferred`` (deducida del nombre) para no afirmar
como anunciado lo que no lo es.
"""

from __future__ import annotations

from dataclasses import dataclass

TOKEN_PARAM_DEFAULT = "max_tokens"
TOKEN_PARAM_REASONING = "max_completion_tokens"

# Parámetros de esfuerzo ('' / "auto" se gestionan aparte: no se envía nada).
EFFORT_PARAM = "reasoning_effort"
THINKING_LEVEL_PARAM = "thinking_level"
OUTPUT_CONFIG_EFFORT_PARAM = "output_config.effort"
BUDGET_TOKENS_PARAM = "budget_tokens"
THINKING_PARAM = "thinking"

# Tipos de control (lo que de verdad viaja al proveedor).
CONTROL_REASONING_EFFORT = "reasoning_effort"
CONTROL_THINKING_LEVEL = "thinking_level"
CONTROL_OUTPUT_CONFIG_EFFORT = "output_config.effort"
CONTROL_BUDGET_TOKENS = "budget_tokens"
CONTROL_PROVIDER_SPECIFIC = "provider-specific"

EFFORT_LEVELS_DEFAULT: tuple[str, ...] = ("low", "medium", "high")
# Valores que significan «sin thinking» (apagar el razonamiento) al elegir «off».
OFF_LEVELS: tuple[str, ...] = ("none", "off")

# Token/temperatura: sólo las familias de razonamiento de OpenAI cambian de parámetro.
_REASONING_PREFIXES: tuple[str, ...] = ("o1", "o3", "o4", "gpt-5")

# Esfuerzo por familia: (prefijos, niveles, valor de «off», parámetro). El valor de «off»
# vacío = esa familia no permite apagar el razonamiento, así que no se ofrece la opción.
_EFFORT_FAMILIES: tuple[tuple[tuple[str, ...], tuple[str, ...], str, str], ...] = (
    (
        ("deepseek-flash", "deepseek-v4-pro", "deepseek-v4", "deepseek-reasoner"),
        ("low", "high", "max"),
        "none",
        EFFORT_PARAM,
    ),
    (_REASONING_PREFIXES, EFFORT_LEVELS_DEFAULT, "", EFFORT_PARAM),
)

# Orden canónico para presentar los niveles (los desconocidos van al final, alfabéticos).
_LEVEL_RANK: dict[str, int] = {"minimal": 0, "low": 1, "medium": 2, "high": 3, "max": 4, "xhigh": 5}

# Interruptor «Think» explícito: canal, valor que enciende, valor que apaga. Se declara por
# proveedor/modelo (tabla o config explícita) y **nunca** se envía si el modelo no lo declara.
THINKING_PARAM = "thinking"
_THINK_FAMILIES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    # DeepSeek (docs oficiales): ``thinking: {type: enabled|disabled}``. ``temperature`` no
    # tiene efecto en thinking mode; ``reasoning_effort`` acepta none/low/high/max.
    (("deepseek",), THINKING_PARAM, "enabled", "disabled"),
)

_CONTROL_BY_PARAM: dict[str, str] = {
    EFFORT_PARAM: CONTROL_REASONING_EFFORT,
    THINKING_LEVEL_PARAM: CONTROL_THINKING_LEVEL,
    OUTPUT_CONFIG_EFFORT_PARAM: CONTROL_OUTPUT_CONFIG_EFFORT,
    BUDGET_TOKENS_PARAM: CONTROL_BUDGET_TOKENS,
    THINKING_PARAM: CONTROL_PROVIDER_SPECIFIC,
}
_PARAM_BY_CONTROL: dict[str, str] = {value: key for key, value in _CONTROL_BY_PARAM.items()}

# Presupuestos de thinking de Anthropic por nivel (``budget_tokens``); sólo se usan cuando
# se configura el control a mano (p. ej. ``llm.models.<id>``), nunca por defecto.
THINKING_BUDGETS: dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "max": 32768,
    "xhigh": 65536,
}
DEFAULT_THINKING_BUDGET = 8192
THINKING_MIN_BUDGET = 1024


@dataclass(frozen=True)
class ModelCapabilities:
    """Cómo hay que hablarle a este modelo."""

    reasoning: bool = False
    token_param: str = TOKEN_PARAM_DEFAULT
    accepts_temperature: bool = True
    # Parámetro y niveles de esfuerzo admitidos ("" / () = este modelo no lo admite).
    effort_param: str = ""
    effort_levels: tuple[str, ...] = ()
    # Valor que apaga el razonamiento ("" = no se puede apagar / no se ofrece «off»).
    effort_off_value: str = ""
    # Tipo de control (qué canal lleva el nivel) y nivel que el proveedor da por defecto.
    effort_control: str = ""
    effort_default: str = ""
    # Interruptor «Think»: canal y valores de encendido/apagado ("" = el modelo no lo declara).
    think_param: str = ""
    think_on: str = ""
    think_off: str = ""


def control_for_param(param: str) -> str:
    """Tipo de control que corresponde a un parámetro de esfuerzo."""
    if not param:
        return ""
    return _CONTROL_BY_PARAM.get(param, CONTROL_PROVIDER_SPECIFIC)


def param_for_control(control: str) -> str:
    """Parámetro que corresponde a un tipo de control ("" si es desconocido)."""
    return _PARAM_BY_CONTROL.get(str(control or "").strip().lower(), "")


def override_entry(model: str, overrides: dict | None) -> dict | None:
    """Entrada de ``llm.models`` aplicable a este modelo (id exacto o prefijo)."""
    if not isinstance(overrides, dict):
        return None
    model_lower = str(model or "").strip().lower()
    fallback: dict | None = None
    for key, value in overrides.items():
        if not isinstance(value, dict):
            continue
        key_lower = str(key).lower()
        if key_lower == model_lower:
            return value
        if model_lower.startswith(key_lower):
            fallback = value
    return fallback


def model_capabilities(model: str, overrides: dict | None = None) -> ModelCapabilities:
    """Capacidades del modelo: tabla por prefijo + sobrescritura explícita."""
    model_lower = (model or "").strip().lower()
    reasoning = any(model_lower.startswith(prefix) for prefix in _REASONING_PREFIXES)
    levels: tuple[str, ...] = ()
    off_value = ""
    param = ""
    think_param = ""
    think_on = ""
    think_off = ""
    for prefixes, family_levels, family_off, family_param in _EFFORT_FAMILIES:
        if any(model_lower.startswith(prefix) for prefix in prefixes):
            levels, off_value, param = family_levels, family_off, family_param
            break
    for prefixes, family_param, family_on, family_off in _THINK_FAMILIES:
        if any(model_lower.startswith(prefix) for prefix in prefixes):
            think_param, think_on, think_off = family_param, family_on, family_off
            break
    caps = ModelCapabilities(
        reasoning=reasoning,
        token_param=TOKEN_PARAM_REASONING if reasoning else TOKEN_PARAM_DEFAULT,
        accepts_temperature=not reasoning,
        effort_param=param,
        effort_levels=levels,
        effort_off_value=off_value,
        effort_control=control_for_param(param),
        think_param=think_param,
        think_on=think_on,
        think_off=think_off,
    )
    return _apply_overrides(model_lower, caps, overrides)


def _apply_overrides(model_lower: str, caps: ModelCapabilities, overrides: dict | None) -> ModelCapabilities:
    """Aplica ``llm.models.<id>`` si existe (por id exacto o por prefijo)."""
    entry = override_entry(model_lower, overrides)
    if not isinstance(entry, dict):
        return caps
    reasoning = bool(entry.get("reasoning", caps.reasoning))
    token_param = str(
        entry.get("token_param")
        or (TOKEN_PARAM_REASONING if reasoning else TOKEN_PARAM_DEFAULT)
    )
    # si no se dice lo contrario, la temperatura va con el razonamiento
    accepts = entry.get("accepts_temperature")
    effort_param = entry.get("effort_param")
    control = entry.get("effort_control", entry.get("control"))
    if effort_param is None and control is not None:
        effort_param = param_for_control(control) or caps.effort_param
    if effort_param is None:
        effort_param = EFFORT_PARAM if reasoning else caps.effort_param
    levels_raw = entry.get("effort_levels")
    if isinstance(levels_raw, (list, tuple)):
        effort_levels = tuple(str(level) for level in levels_raw if str(level).strip())
    elif effort_param:
        effort_levels = caps.effort_levels or EFFORT_LEVELS_DEFAULT
    else:
        effort_levels = caps.effort_levels
    off_raw = entry.get("effort_off_value", entry.get("off_value"))
    effort_off_value = caps.effort_off_value if off_raw is None else str(off_raw)
    if effort_levels and not effort_param:
        # Declarar niveles implica que el parámetro existe (p. ej. un override para un modelo
        # que la tabla de familias todavía no conoce).
        effort_param = EFFORT_PARAM
    default_raw = entry.get("effort_default", entry.get("default_level"))
    effort_default = caps.effort_default if default_raw is None else str(default_raw)
    think_param, think_on, think_off = caps.think_param, caps.think_on, caps.think_off
    think_raw = entry.get("thinking")
    if isinstance(think_raw, dict):
        think_param = str(think_raw.get("param") or think_param or THINKING_PARAM)
        think_on = str(think_raw.get("on") or think_on or "enabled")
        think_off = str(think_raw.get("off") or think_off or "")
    elif isinstance(think_raw, bool):
        think_param = think_param or THINKING_PARAM
        think_on = think_on or "enabled"
        think_off = think_off or ("disabled" if think_raw else "")
    if entry.get("think_param"):
        think_param = str(entry["think_param"])
    if entry.get("think_on"):
        think_on = str(entry["think_on"])
    if "think_off" in entry:
        think_off = str(entry.get("think_off") or "")
    return ModelCapabilities(
        reasoning=reasoning,
        token_param=token_param,
        accepts_temperature=(not reasoning) if accepts is None else bool(accepts),
        effort_param=str(effort_param or ""),
        effort_levels=effort_levels,
        effort_off_value=str(effort_off_value or ""),
        effort_control=control_for_param(str(effort_param or "")),
        effort_default=effort_default,
        think_param=str(think_param or ""),
        think_on=str(think_on or ""),
        think_off=str(think_off or ""),
    )


def _ordered_levels(levels) -> list[str]:
    """Niveles sin repetir, en orden canónico y con los desconocidos al final."""
    unicos = list(dict.fromkeys(str(level).strip().lower() for level in levels if str(level).strip()))
    return sorted(unicos, key=lambda nivel: (_LEVEL_RANK.get(nivel, 99), nivel))


@dataclass(frozen=True)
class EffortResolution:
    """Resultado de combinar tabla + override + lo anunciado por el proveedor."""

    param: str = ""
    levels: tuple[str, ...] = ()
    off: str = ""
    default: str = ""
    control: str = ""
    source: str = "none"  # announced | override | table | none
    think_param: str = ""
    think_on: str = ""
    think_off: str = ""


def _resolve(model: str, overrides: dict | None, announced: dict | None) -> EffortResolution:
    """Combina la tabla de familias, ``llm.models.<id>`` y lo que anuncie la API."""
    model_lower = (model or "").strip().lower()
    caps = model_capabilities(model, overrides)
    levels = list(caps.effort_levels)
    off = caps.effort_off_value
    default = caps.effort_default
    param = caps.effort_param
    control = caps.effort_control
    if override_entry(model_lower, overrides) is not None:
        source = "override"
    elif levels or param:
        source = "table"
    else:
        source = "none"
    if isinstance(announced, dict) and announced:
        anunciado_control = str(announced.get("control") or "").strip().lower()
        if anunciado_control:
            control = anunciado_control
            param = param_for_control(control) or param
        anunciados = announced.get("supported_levels")
        if isinstance(anunciados, (list, tuple)):
            for level in anunciados:
                value = str(level).strip().lower()
                if not value:
                    continue
                if value in OFF_LEVELS:
                    off = off or value
                elif value not in levels:
                    levels.append(value)
        anunciado_default = announced.get("default_level") or announced.get("default")
        if anunciado_default:
            default = str(anunciado_default)
        if announced.get("can_disable") is True:
            off = off or OFF_LEVELS[0]
        source = "announced"
    if not param and (levels or off):
        param = EFFORT_PARAM
    if param and not control:
        control = control_for_param(param)
    return EffortResolution(
        param=param,
        levels=tuple(levels),
        off=off,
        default=default,
        control=control,
        source=source,
        think_param=caps.think_param,
        think_on=caps.think_on,
        think_off=caps.think_off,
    )


def effort_options(
    model: str, overrides: dict | None = None, announced: dict | None = None
) -> tuple[str, ...]:
    """Niveles de esfuerzo ofrecibles para este modelo.

    Devuelve ``("auto", "off", …niveles)`` cuando el modelo admite el parámetro (``off`` sólo
    si se puede apagar), o ``()`` cuando no lo admite (la UI desactiva el control). Combina la
    tabla de capacidades con lo que anuncie la API; nunca ofrece un valor que no venga de
    ninguna de las dos fuentes.
    """
    res = _resolve(model, overrides, announced)
    can_off = bool(res.off) or bool(res.think_param and res.think_off)
    if not res.param and not (res.think_param and res.think_on):
        return ()
    if not (res.levels or res.off or can_off):
        return ()
    opciones = ["auto"]
    if can_off:
        opciones.append("off")
    opciones.extend(_ordered_levels(res.levels))
    return tuple(opciones)


def effort_metadata(
    model: str, overrides: dict | None = None, announced: dict | None = None
) -> dict:
    """Metadata presentable del esfuerzo: niveles, control, origen y si es inferido.

    ``inferred`` = los niveles salen **sólo** de la tabla por prefijo de nombre (no de un
    anuncio del proveedor ni de un override): se marca para no afirmar como declarado lo que
    se dedujo del nombre.
    """
    res = _resolve(model, overrides, announced)
    can_off = bool(res.off) or bool(res.think_param and res.think_off)
    return {
        "options": list(effort_options(model, overrides, announced)),
        "levels": _ordered_levels(res.levels),
        "default": res.default,
        "control": res.control,
        "param": res.param,
        "can_disable": can_off,
        "off_value": res.off,
        "reasoning": bool(res.levels or res.param or res.think_param),
        "think_param": res.think_param,
        "think_on": res.think_on,
        "think_off": res.think_off,
        "source": res.source,
        "inferred": res.source == "table",
    }


def resolve_effort(
    model: str,
    overrides: dict | None,
    requested: str,
    announced: dict | None = None,
    thinking: bool | None = None,
) -> str | None:
    """Valor a enviar al proveedor para el nivel pedido, o ``None`` si no aplica.

    ``auto``/vacío → ``None`` (no se envía el parámetro). ``off`` → el valor que apaga el
    razonamiento (p. ej. ``none``) sólo si esa familia lo admite. Un nivel se envía sólo si
    está entre los admitidos: nunca se manda un valor inventado.

    ``thinking=False`` con un canal Think propio (p. ej. DeepSeek ``thinking``) apaga por ese
    canal y **no** añade un ``reasoning_effort`` de más; sin canal Think se usa el valor ``off``
    declarado. ``None`` = comportamiento clásico (sólo esfuerzo).
    """
    value = str(requested or "").strip().lower()
    res = _resolve(model, overrides, announced)
    if thinking is False:
        if res.think_param and res.think_off:
            return None
        return res.off or None
    if not value or value == "auto":
        return None
    if not res.param:
        return None
    if value == "off":
        return res.off or None
    return value if value in res.levels else None


def resolve_think(
    model: str,
    overrides: dict | None,
    thinking: bool | None,
) -> dict | None:
    """Bloque del interruptor «Think» a inyectar, o ``None`` si no aplica.

    Sólo se emite cuando el modelo declara un canal Think (tabla o config explícita del
    proveedor); nunca se inventa para un modelo que no lo soporta.
    """
    if thinking is None:
        return None
    caps = model_capabilities(model, overrides)
    if not caps.think_param:
        return None
    if thinking:
        return {caps.think_param: {"type": caps.think_on or "enabled"}}
    if not caps.think_off:
        return None
    return {caps.think_param: {"type": caps.think_off}}


def thinking_budget(level: str, entry: dict | None = None) -> int:
    """Presupuesto de ``budget_tokens`` para un nivel (override explícito, si lo hay)."""
    entry = entry if isinstance(entry, dict) else {}
    explicit = entry.get("thinking_budget", entry.get("budget_tokens"))
    if explicit is not None:
        try:
            return max(THINKING_MIN_BUDGET, int(explicit))
        except (TypeError, ValueError):
            pass
    return THINKING_BUDGETS.get(str(level or "").strip().lower(), DEFAULT_THINKING_BUDGET)


def thinking_payload(level: str, entry: dict | None = None) -> dict | None:
    """Bloque ``thinking`` de Anthropic para un nivel, o ``None`` si se apaga.

    ``adaptive`` sólo si el override lo declara (``thinking_type: adaptive``); si no, se usa
    ``budget_tokens`` (el canal documentado para el thinking extendido).
    """
    value = str(level or "").strip().lower()
    if not value or value in OFF_LEVELS:
        return None
    entry = entry if isinstance(entry, dict) else {}
    if str(entry.get("thinking_type") or "").strip().lower() == "adaptive":
        return {"type": "adaptive"}
    return {"type": "enabled", "budget_tokens": thinking_budget(value, entry)}
