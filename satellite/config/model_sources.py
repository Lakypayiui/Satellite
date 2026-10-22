"""Estrategias explícitas de descubrimiento de modelos por proveedor/familia.

No todos los proveedores exponen ``GET <base>/models`` con la misma forma. Aquí vive
**una entrada por proveedor** con lo que de verdad cambia, según su documentación:

- ``openai``      → ``GET {base}/models`` → ``{"data": [...]}``.
- ``anthropic``   → ``GET {base}/v1/models`` → ``{"data": [...], "has_more", "first_id",
  "last_id"}``; la paginación va con ``after_id``/``before_id`` + ``limit`` y ``x-api-key``.
- ``deepseek``    → ``GET {base}/models`` (metadata de esfuerzo cuando la publica).
- ``xai``         → ``GET {base}/v1/language-models`` (ruta distinta de ``/models``).
- ``gemini``      → listado **nativo** paginado ``GET {base-native}/models`` con
  ``pageToken``/``nextPageToken``; si no se puede, se cae a su forma OpenAI-compatible
  (``{base}/models``) sin rediseñar el proveedor (ver ``note``).
- ``groq``        → ``GET {base}/openai/v1/models`` → ``{"data": [...]}``.
- ``openrouter``  → ``GET {base}/models`` con ``supported_parameters`` por modelo.
- ``together``    → ``GET {base}/models`` con **arreglo raíz** (sin envoltorio).
- ``cohere``      → ``GET {base-native}/v1/models`` → ``{"models": [...],
  "next_page_token"}``; la forma compatible no lista modelos igual.
- ``fireworks``   → ``GET {base}/models`` o, si hay ``account_id``, el endpoint por
  cuenta (``/v1/accounts/{account_id}/models``). Sin ``account_id`` queda la vía normal.
- ``mistral``     → ``GET {base}/models`` → ``{"data": [...]}``.
- ``commandcode`` → ``GET {base}/models?view=client`` (el catálogo que sirve al cliente).
  Es el mismo endpoint para las dos entradas (OpenAI y Anthropic): su **edge rechaza con
  403** a quien no manda un ``User-Agent`` propio (un cliente HTTP genérico manda el suyo),
  así que hay que identificarse; no usa clave.

El resto del sistema (:mod:`satellite.config.models`) sólo **consume** la estrategia: así
no se asume que todos usan ``/models`` y una ruta nueva se añade en un único sitio, sin
inventarla.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, replace

from satellite.config.catalog import ANTHROPIC, OPENAI

# Estilos de paginación documentados.
PAGINATION_NONE = "none"
PAGINATION_HAS_MORE = "has_more"  # has_more + token por defecto (best-effort)
PAGINATION_ANTHROPIC = "anthropic"  # after_id / before_id + limit
PAGINATION_GOOGLE = "google"  # pageToken / nextPageToken (nativo)
PAGINATION_COHERE = "cohere"  # next_page_token

# Forma de la respuesta.
SHAPE_AUTO = "auto"
SHAPE_ROOT = "root"
SHAPE_MODELS = "models"
SHAPE_DATA = "data"

# Cabeceras de autenticación.
HEADERS_BEARER = "bearer"
HEADERS_OPTIONAL_BEARER = "optional_bearer"
HEADERS_NONE = "none"
HEADERS_ANTHROPIC = "anthropic"
HEADERS_GOOGLE = "google"

# Cómo leer capacidades de la metadata del modelo.
CAPS_AUTO = ""
CAPS_OPENROUTER = "openrouter"
CAPS_GEMINI = "gemini"
CAPS_COHERE = "cohere"

ANTHROPIC_VERSION = "2023-06-01"

# El catálogo de CommandCode responde 403 a los clientes con el User-Agent por defecto de
# urllib; el cliente oficial se identifica como ``command-code/<versión>`` y pide la vista
# de cliente con ``view=client``. Mismo endpoint para las dos familias (OpenAI y Anthropic).
COMMANDCODE_USER_AGENT = "command-code/satellite"
COMMANDCODE_MODELS_QUERY = "view=client"

DEFAULT_BASES: dict[str, str] = {
    OPENAI: "https://api.openai.com/v1",
    ANTHROPIC: "https://api.anthropic.com",
}

FIREWORKS_KEY = "fireworks"
GEMINI_KEY = "gemini"

# Endpoint por cuenta de Fireworks (plano de control), documentado aparte del de inferencia.
FIREWORKS_ACCOUNTS = "https://api.fireworks.ai/v1/accounts/{account}/models"


@dataclass(frozen=True)
class ModelSource:
    """Lo que cambia entre proveedores al listar modelos."""

    key: str
    hosts: tuple[str, ...] = ()
    family: str = OPENAI
    # Ruta a añadir a la base ("" = ``/models``, o ``/v1/models`` en anthropic).
    models_path: str = ""
    # Consulta que el endpoint exige para su vista de cliente ("" = sin query).
    models_query: str = ""
    # ``User-Agent`` a enviar ("" = el del cliente HTTP). Hay catálogos que lo exigen.
    user_agent: str = ""
    # Sufijo que hay que garantizar en la base antes de añadir ``models_path``.
    ensure_suffix: str = ""
    # Sufijo a quitar de la base (p. ej. Gemini: la base es la compatible ``…/openai``).
    strip_suffix: str = ""
    pagination: str = PAGINATION_NONE
    shape: str = SHAPE_AUTO
    headers: str = HEADERS_BEARER
    capabilities: str = CAPS_AUTO
    note: str = ""


# Orden = prioridad al resolver por host (coincidencia exacta de host o subdominio).
SOURCES: tuple[ModelSource, ...] = (
    ModelSource("openai", hosts=("api.openai.com",)),
    # CommandCode serves its catalog at GET /provider/v1/models?view=client without a key:
    # the API key is used on generation endpoints, not on this catalog request. The edge
    # rejects a missing/urllib User-Agent with 403, so the client identifies itself.
    ModelSource(
        "commandcode",
        hosts=("api.commandcode.ai",),
        models_query=COMMANDCODE_MODELS_QUERY,
        headers=HEADERS_NONE,
        user_agent=COMMANDCODE_USER_AGENT,
    ),
    ModelSource(
        "anthropic", hosts=("api.anthropic.com",), family=ANTHROPIC,
        pagination=PAGINATION_ANTHROPIC, headers=HEADERS_ANTHROPIC,
    ),
    ModelSource("deepseek", hosts=("api.deepseek.com",)),
    ModelSource(
        "xai", hosts=("api.x.ai",),
        models_path="/language-models", ensure_suffix="/v1",
        note="xAI publica su listado en /v1/language-models, no en /v1/models.",
    ),
    ModelSource(
        GEMINI_KEY, hosts=("generativelanguage.googleapis.com",),
        models_path="/models", strip_suffix="/openai",
        pagination=PAGINATION_GOOGLE, shape=SHAPE_MODELS, headers=HEADERS_GOOGLE,
        capabilities=CAPS_GEMINI,
        note=(
            "Gemini se lista con su API nativa paginada (pageToken) y thinking_level; el "
            "runtime habla su forma OpenAI-compatible, así que si la nativa no responde se "
            "usa esa y el control de thinking queda como limitación (no se envía)."
        ),
    ),
    ModelSource("groq", hosts=("api.groq.com",)),
    ModelSource("openrouter", hosts=("openrouter.ai",), capabilities=CAPS_OPENROUTER),
    ModelSource("together", hosts=("api.together.xyz",), shape=SHAPE_ROOT),
    ModelSource(
        "cohere", hosts=("api.cohere.ai", "api.cohere.com"),
        models_path="/models", ensure_suffix="/v1", strip_suffix="/compatibility/v1",
        pagination=PAGINATION_COHERE, shape=SHAPE_MODELS, capabilities=CAPS_COHERE,
        note="Cohere lista modelos en /v1/models (models + next_page_token) con Bearer.",
    ),
    ModelSource(FIREWORKS_KEY, hosts=("api.fireworks.ai",)),
    ModelSource("mistral", hosts=("api.mistral.ai",)),
)

_BY_KEY = {source.key: source for source in SOURCES}


def _norm_family(family: str) -> str:
    return ANTHROPIC if str(family).lower() == ANTHROPIC else OPENAI


def host_of(url: str) -> str:
    """Host de una URL (``""`` si no se puede leer)."""
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def source_for(base_url: str = "", family: str = OPENAI, provider: str = "") -> ModelSource:
    """Estrategia del proveedor: por clave explícita, por host y, si no, la de la familia.

    La clave explícita (clave del catálogo, p. ej. ``groq``) manda; sirve cuando la URL es
    manual. Si no, se reconoce el proveedor por su host, que es lo único que guarda un
    proveedor ya configurado (``name`` es la familia, no el proveedor).
    """
    key = str(provider or "").strip().lower()
    if key and key in _BY_KEY:
        return _BY_KEY[key]
    host = host_of(base_url)
    if host:
        for source in SOURCES:
            if any(host == known or host.endswith("." + known) for known in source.hosts):
                return source
    return _BY_KEY[ANTHROPIC] if _norm_family(family) == ANTHROPIC else _BY_KEY[OPENAI]


def _strip(base: str, suffix: str) -> str:
    if suffix and base.endswith(suffix):
        return base[: -len(suffix)].rstrip("/")
    return base


def _ensure(base: str, suffix: str) -> str:
    if suffix and not base.endswith(suffix):
        return base.rstrip("/") + suffix
    return base


def models_endpoint(
    source: ModelSource, base_url: str = "", family: str = OPENAI, account_id: str = ""
) -> str:
    """URL del listado de modelos para esa estrategia (con su ``view``, si lo pide)."""
    return _add_query(
        _models_path(source, base_url, family, account_id), source.models_query
    )


def _add_query(url: str, query: str) -> str:
    """Añade la consulta del endpoint sin pisar una que ya trajera la URL."""
    if not query:
        return url
    return f"{url}{'&' if '?' in url else '?'}{query}"


def _models_path(
    source: ModelSource, base_url: str = "", family: str = OPENAI, account_id: str = ""
) -> str:
    """URL del listado de modelos, sin la consulta de vista del proveedor."""
    fam = _norm_family(family)
    base = str(base_url or "").rstrip("/") or DEFAULT_BASES[fam]
    if source.key == FIREWORKS_KEY and str(account_id or "").strip():
        return FIREWORKS_ACCOUNTS.format(account=str(account_id).strip())
    if base.endswith("/models") and not source.models_path:
        return base
    if source.strip_suffix:
        base = _strip(base, source.strip_suffix)
    if source.models_path:
        base = _ensure(base, source.ensure_suffix)
        if base.endswith("/models"):
            return base
        return base + source.models_path
    if base.endswith("/models"):
        return base
    if fam == ANTHROPIC:
        return base + "/models" if base.endswith("/v1") else base + "/v1/models"
    return base + "/models"


def models_url(base_url: str = "", family: str = OPENAI, provider: str = "") -> str:
    """URL del endpoint de modelos (atajo de :func:`models_endpoint`)."""
    return models_endpoint(source_for(base_url, family, provider), base_url, family)


def candidate_endpoints(
    source: ModelSource, base_url: str = "", family: str = OPENAI, account_id: str = ""
) -> list[tuple[str, ModelSource]]:
    """Endpoints a probar **en orden**: el del proveedor y, si aplica, un respaldo.

    Gemini primero prueba su API nativa (paginada) y, si no responde, su forma
    OpenAI-compatible. El resto de proveedores sólo tienen uno.
    """
    fam = _norm_family(family)
    out: list[tuple[str, ModelSource]] = [
        (models_endpoint(source, base_url, fam, account_id), source)
    ]
    if source.key == GEMINI_KEY:
        base = str(base_url or "").rstrip("/") or DEFAULT_BASES[fam]
        compat = base if base.endswith("/openai") else base + "/openai"
        compat_source = replace(
            source,
            key="gemini-openai",
            models_path="",
            ensure_suffix="",
            strip_suffix="",
            pagination=PAGINATION_NONE,
            shape=SHAPE_AUTO,
            headers=HEADERS_BEARER,
            capabilities=CAPS_AUTO,
        )
        out.append((compat + "/models", compat_source))
    return out


def headers_for(source: ModelSource, api_key: str | None, family: str = OPENAI) -> dict[str, str]:
    """Cabeceras de la petición: autenticación del proveedor + su ``User-Agent`` si lo exige."""
    headers = _auth_headers(source, api_key)
    if source.user_agent:
        headers["User-Agent"] = source.user_agent
    return headers


def _auth_headers(source: ModelSource, api_key: str | None) -> dict[str, str]:
    """Cabeceras de autenticación según lo documentado para el proveedor."""
    if source.headers == HEADERS_NONE:
        return {}
    if source.headers == HEADERS_GOOGLE:
        return {"x-goog-api-key": api_key} if api_key else {}
    if source.headers == HEADERS_ANTHROPIC:
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    if source.headers == HEADERS_OPTIONAL_BEARER:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return {"Authorization": f"Bearer {api_key}" if api_key else "Bearer not-needed"}


def next_page_after(source: ModelSource, payload: object) -> tuple[str, str] | None:
    """``(parámetro, token)`` si la respuesta anuncia más páginas; ``None`` si no.

    Sólo se pagina cuando el proveedor lo dice, así no se le mandan parámetros que no
    entienda.
    """
    if not isinstance(payload, dict):
        return None
    if source.pagination == PAGINATION_GOOGLE:
        token = payload.get("nextPageToken")
        return ("pageToken", str(token)) if token else None
    if source.pagination == PAGINATION_COHERE:
        token = payload.get("next_page_token")
        return ("next_page_token", str(token)) if token else None
    if source.pagination == PAGINATION_ANTHROPIC:
        if not payload.get("has_more"):
            return None
        token = payload.get("last_id") or payload.get("lastId")
        return ("after_id", str(token)) if token else None
    if source.pagination == PAGINATION_HAS_MORE:
        if not payload.get("has_more"):
            return None
        token = payload.get("last_id") or payload.get("next") or payload.get("next_page_token")
        return ("after", str(token)) if token else None
    return None


def page_params(source: ModelSource, param: str, token: str, page_limit: int) -> dict[str, str]:
    """Parámetros de la página siguiente (el tope cambia de nombre según el proveedor)."""
    params = {param: token}
    if source.pagination == PAGINATION_GOOGLE:
        params["pageSize"] = str(page_limit)
    elif source.pagination == PAGINATION_ANTHROPIC:
        params["limit"] = str(page_limit)
    return params


def normalize_model_id(model_id: str, source: ModelSource | None = None) -> str:
    """Id del modelo tal como se usa en el runtime.

    Gemini publica ``models/gemini-2.5-pro`` en su API nativa; el runtime usa ``gemini-2.5-pro``.
    """
    value = str(model_id or "").strip()
    if source is not None and source.capabilities == CAPS_GEMINI and value.startswith("models/"):
        return value[len("models/") :]
    return value
