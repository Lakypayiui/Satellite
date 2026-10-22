"""Listado de modelos de un proveedor (cliente HTTP httpx).

Portado de la lógica de modelos de la consola web (ya retirada), con tres mejoras:

- las rutas/paginación/parsing **por proveedor** salen de :mod:`satellite.config.model_sources`
  (xAI ``/v1/language-models``, Gemini nativo paginado, Anthropic ``after_id``, Cohere
  ``next_page_token``, Together arreglo raíz, Fireworks por cuenta…), no de asumir ``/models``;
- ``anthropic`` usa ``x-api-key`` + ``anthropic-version`` (con ``Bearer`` nunca funcionaba);
- además de los ids, se conserva la **metadata de capacidades** que anuncie el proveedor
  (niveles de esfuerzo, control, si se puede desactivar), sin inventarla.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field

import httpx

from satellite.config.catalog import ANTHROPIC, OPENAI
from satellite.config.model_sources import (
    CAPS_COHERE,
    CAPS_GEMINI,
    CAPS_OPENROUTER,
    candidate_endpoints,
    headers_for,
    models_url as models_url,
    next_page_after,
    normalize_model_id,
    page_params,
    source_for,
)

DEFAULT_TIMEOUT_S = 10.0
PAGE_LIMIT = 1000
MAX_PAGES = 20
AUTH_STATUS = (401, 403)
_HTTP_SCHEMES = frozenset({"http", "https"})

# Niveles que significan «apagar el razonamiento» cuando el proveedor los anuncia.
_OFF_LEVELS: tuple[str, ...] = ("none", "off", "disabled")


def _scheme(url: str) -> str:
    """Esquema normalizado de una URL; vacío si no se puede parsear."""
    try:
        return urllib.parse.urlsplit(str(url or "")).scheme.lower()
    except ValueError:
        return ""


@dataclass(frozen=True)
class ModelsResult:
    """Resultado del listado: modelos, URL probada, estado HTTP y error (si lo hubo)."""

    ok: bool
    models: tuple[str, ...] = ()
    url: str = ""
    error: str = ""
    details: dict = field(default_factory=dict)
    # Capacidades anunciadas por el proveedor, normalizadas por modelo.
    capabilities: dict = field(default_factory=dict)
    # Código HTTP de la última respuesta (0 si ni siquiera hubo respuesta).
    status: int = 0
    # ``True`` cuando el rechazo es de credencial (401/403): la UI lo trata aparte.
    auth_error: bool = False
    # Proveedor reconocido (clave de la estrategia): útil para decir «qué se probó».
    provider: str = ""

    def context_for(self, model: str) -> int | None:
        """Longitud de contexto que **declara el proveedor** para ese modelo."""
        wanted = str(model or "")
        entry = self.details.get(wanted)
        if entry is None and wanted:
            for key, value in self.details.items():
                if key.endswith(wanted) or wanted.endswith(key):
                    entry = value
                    break
        return _context_from_entry(entry) if isinstance(entry, dict) else None


def _payload_sources(payload: object) -> list:
    """Listas de modelos dentro del payload, en las formas que se ven en la práctica."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for campo in ("data", "models", "model"):
        value = payload.get(campo)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            inner = value.get("data") or value.get("models") or value.get("model")
            if isinstance(inner, list):
                return inner
            # índice por id: {"llama3": {...}}
            if value and all(isinstance(v, dict) for v in value.values()):
                return [{"id": key, **item} for key, item in value.items()]
    return []


def _parse_details(payload: object, source=None) -> dict[str, dict]:
    """Modelos con su metadata completa (id → entrada): conserva **todos** los ids."""
    details: dict[str, dict] = {}
    for item in _payload_sources(payload):
        if isinstance(item, str):
            model_id = normalize_model_id(item, source)
            if model_id:
                details.setdefault(model_id, {"id": model_id})
            continue
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or item.get("name") or item.get("model") or "")
        model_id = normalize_model_id(raw, source)
        if model_id:
            details[model_id] = item
    return details


def _effort_meta(entry: object) -> dict:
    """Metadatos de esfuerzo que anuncie el proveedor para un modelo (tolerante).

    Acepta ``{"effort": {"supported_levels": [...], "default_level": ..., "control": ...}}``
    y variantes (``levels``, ``default``, o las claves al mismo nivel que el modelo). Si no
    hay nada, ``{}``: no se inventan capacidades.
    """
    if not isinstance(entry, dict):
        return {}
    fuentes = [entry]
    for key in ("capabilities", "metadata", "meta", "details", "settings", "architecture"):
        value = entry.get(key)
        if isinstance(value, dict):
            fuentes.append(value)
    for source in fuentes:
        effort = source.get("effort")
        if isinstance(effort, dict):
            out: dict = {}
            levels = effort.get("supported_levels") or effort.get("levels")
            if isinstance(levels, list):
                out["supported_levels"] = [str(l) for l in levels if str(l).strip()]
            default = effort.get("default_level") or effort.get("default")
            if default:
                out["default_level"] = str(default)
            control = effort.get("control") or effort.get("param") or effort.get("type")
            if control:
                out["control"] = str(control)
            can_disable = effort.get("can_disable")
            if isinstance(can_disable, bool):
                out["can_disable"] = can_disable
            if out:
                return out
        levels = source.get("supported_levels")
        if isinstance(levels, list):
            out = {"supported_levels": [str(l) for l in levels if str(l).strip()]}
            default = source.get("default_level")
            if default:
                out["default_level"] = str(default)
            control = source.get("control") or source.get("effort_param")
            if control:
                out["control"] = str(control)
            return out
    return {}


def _capability_info(entry: object, source=None) -> dict:
    """Capacidades de un modelo, tal como las anunció el proveedor (marcadas como tales)."""
    if not isinstance(entry, dict):
        return {}
    info: dict = {}
    effort = dict(_effort_meta(entry))
    niveles = effort.get("supported_levels") or []
    if "can_disable" in effort:
        info["can_disable"] = bool(effort["can_disable"])
    elif any(str(nivel).strip().lower() in _OFF_LEVELS for nivel in niveles):
        info["can_disable"] = True
    if isinstance(entry.get("reasoning"), bool):
        info["reasoning"] = bool(entry["reasoning"])
    control = ""
    style = source.capabilities if source is not None else ""
    if style == CAPS_OPENROUTER:
        params = entry.get("supported_parameters")
        if isinstance(params, list):
            nombres = {str(p) for p in params}
            info["supported_parameters"] = sorted(nombres)
            if "reasoning_effort" in nombres:
                info["reasoning"] = True
                control = "reasoning_effort"
            elif nombres & {"reasoning", "include_reasoning"}:
                info["reasoning"] = True
    elif style == CAPS_GEMINI:
        methods = entry.get("supportedGenerationMethods")
        if isinstance(methods, list) and methods:
            info["methods"] = [str(m) for m in methods]
        if "thinking" in entry or "thinkingLevel" in entry or "thinking_level" in entry:
            info["reasoning"] = True
            control = "thinking_level"
        if control:
            # El thinking_level de Gemini sólo existe en su API nativa: se declara como
            # limitación para no ofrecer un control que la forma compatible no puede enviar.
            info["native_only"] = True
    elif style == CAPS_COHERE:
        endpoints = entry.get("endpoints")
        if isinstance(endpoints, list) and endpoints:
            info["endpoints"] = [str(e) for e in endpoints]
    if control:
        effort.setdefault("control", control)
    if effort:
        info["effort"] = effort
    if info:
        info["source"] = "announced"
    return info


def capabilities_details(details: dict, source=None) -> dict:
    """Capacidades normalizadas por modelo, conservando lo que anuncie el proveedor."""
    out: dict = {}
    for model_id, entry in (details or {}).items():
        info = _capability_info(entry, source)
        if info:
            out[str(model_id)] = info
    return out


def _with_query(url: str, params: dict[str, str]) -> str:
    """URL con parámetros añadidos sin pisar los que ya llevara."""
    if not params:
        return url
    query = "&".join(
        f"{key}={urllib.parse.quote(str(value))}" for key, value in params.items()
    )
    return f"{url}{'&' if '?' in url else '?'}{query}"


# Nombres con los que distintos proveedores publican la ventana de contexto.
# OJO: `max_tokens` **no** está aquí a propósito — en Anthropic y en los
# endpoints OpenAI-compatible es el tope de **salida**, no la ventana; tomarlo
# como ventana adelantaría el auto-compactado.
_CONTEXT_KEYS: tuple[str, ...] = (
    "context_length",
    "max_context_length",
    "context_window",
    "max_model_len",
    "max_context_tokens",
    "max_input_tokens",
    "n_ctx",
    "inputTokenLimit",
    "context",
)
_NESTED_KEYS: tuple[str, ...] = ("meta", "metadata", "details", "architecture", "settings")


def _context_from_entry(entry: dict | None) -> int | None:
    """Busca la ventana de contexto en la entrada del modelo (varias formas)."""
    if not isinstance(entry, dict):
        return None
    for source in (entry, *(entry.get(key) for key in _NESTED_KEYS)):
        if not isinstance(source, dict):
            continue
        for key in _CONTEXT_KEYS:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            try:
                tokens = int(value)
            except (TypeError, ValueError):
                continue
            if tokens > 0:
                return tokens
    return None


def _walk_pages(
    url: str,
    source,
    family: str,
    api_key: str | None,
    timeout: float,
    max_pages: int,
) -> tuple[dict[str, dict], int, str]:
    """Recorre una URL (y sus páginas) devolviendo ``(detalles, estado, error)``."""
    details: dict[str, dict] = {}
    status = 0
    error = ""
    current = url
    headers = headers_for(source, api_key, family)
    try:
        # Sin redirecciones: un 3xx no puede desviar la petición (p. ej. a file:).
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            for _ in range(max(1, max_pages)):
                scheme = _scheme(current)
                if scheme not in _HTTP_SCHEMES:
                    error = (
                        f"URL no permitida: esquema {scheme or 'vacío'}; "
                        "usa http o https"
                    )
                    break
                try:
                    response = client.get(current, headers=headers)
                    status = int(response.status_code)
                except Exception as exc:  # noqa: BLE001 — se reporta tal cual al usuario
                    error = f"{type(exc).__name__}: {exc}"
                    break
                if status >= 300:
                    error = f"HTTP {status} {response.reason_phrase}".rstrip()
                    # Preserve only safe, structured provider diagnostics. Never surface an
                    # arbitrary response body (which could contain echoed credentials/data).
                    try:
                        payload = json.loads(
                            response.content[:16_384].decode("utf-8", errors="replace")
                        )
                    except (UnicodeError, json.JSONDecodeError):
                        payload = None
                    if isinstance(payload, dict):
                        detail = payload.get("error", payload)
                        if isinstance(detail, dict):
                            code = detail.get("code") or detail.get("type")
                            message = detail.get("message")
                            parts = []
                            if isinstance(code, str) and code.strip():
                                parts.append(code.strip()[:100])
                            if isinstance(message, str) and message.strip():
                                safe_message = " ".join(message.split())[:240]
                                parts.append(safe_message)
                            if parts:
                                error += ": " + " — ".join(parts)
                    break
                try:
                    payload = json.loads(
                        response.content.decode("utf-8", errors="replace")
                    )
                except json.JSONDecodeError as exc:
                    error = f"respuesta no-JSON: {exc}"
                    break
                details.update(_parse_details(payload, source))
                siguiente = next_page_after(source, payload)
                if siguiente is None:
                    break
                token, parametro = siguiente
                siguiente_url = _with_query(
                    url, page_params(source, parametro, token, PAGE_LIMIT)
                )
                if siguiente_url == current:  # el proveedor no avanza: se corta para no dar vueltas
                    break
                current = siguiente_url
    except Exception as exc:  # noqa: BLE001 — fallos al crear/cerrar el cliente HTTP
        error = f"{type(exc).__name__}: {exc}"
    return details, status, error


def fetch_models(
    base_url: str = "",
    family: str = OPENAI,
    api_key: str | None = None,
    *,
    provider: str = "",
    account_id: str = "",
    timeout: float = DEFAULT_TIMEOUT_S,
    max_pages: int = MAX_PAGES,
) -> ModelsResult:
    """Lista los modelos del proveedor; nunca lanza, siempre informa.

    Resuelve la estrategia del proveedor (por clave explícita o por host) y usa su ruta,
    cabeceras, forma de respuesta y paginación documentadas. Sólo pagina si la respuesta lo
    anuncia, así no se le mandan parámetros que no entienda. Si hay un respaldo documentado
    (Gemini → forma OpenAI-compatible) se prueba después. Conserva **todos** los ids que
    lleguen (deduplicados y ordenados) y su metadata de capacidades. El estado HTTP se
    devuelve para distinguir un rechazo de credencial (401/403).
    """
    family = ANTHROPIC if str(family).lower() == ANTHROPIC else OPENAI
    source = source_for(base_url, family, provider)
    details: dict[str, dict] = {}
    status = 0
    error = ""
    used_url = ""
    for url, src in candidate_endpoints(source, base_url, family, account_id):
        page_details, page_status, page_error = _walk_pages(
            url, src, family, api_key, timeout, max_pages
        )
        used_url = url
        if page_details:
            details.update(page_details)
            status = page_status
            error = page_error
            break
        status = page_status or status
        error = page_error or error
    if details:
        # Si una página posterior falló, se devuelven los modelos que sí llegaron, con la nota.
        return ModelsResult(
            ok=True,
            models=tuple(sorted(details)),
            url=used_url,
            error=error,
            details=details,
            capabilities=capabilities_details(details, source),
            status=status,
            provider=source.key,
        )
    if not error:
        return ModelsResult(
            ok=True,
            url=used_url,
            error="la respuesta no traía modelos ('data'/'models' vacío)",
            status=status,
            provider=source.key,
        )
    return ModelsResult(
        ok=False,
        url=used_url,
        error=error,
        status=status,
        auth_error=status in AUTH_STATUS,
        provider=source.key,
    )


def default_model(models: tuple[str, ...] | list[str]) -> str:
    """Modelo sugerido: el primero (ya vienen ordenados) o cadena vacía."""
    return str(models[0]) if models else ""
