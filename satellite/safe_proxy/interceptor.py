"""Modo **interno**: la política del Safe Proxy dentro del ciclo del proveedor.

En vez de levantar un listener y pasar las peticiones por loopback, el saneado se aplica
**en el mismo proceso**, justo antes de enviar al proveedor y justo después de recibir:

    mensajes → [política + ofuscación + enmascarado] → proveedor → [restauración] → respuesta

Sin HTTP, sin puerto, sin roundtrip. Usa el **mismo** :class:`SafeProxyCore` que el modo
servidor (misma config, mismo estado, mismos interruptores), así que lo que se configura en
Ajustes se aplica aquí de verdad.

Qué NO se aplica en interno (a diferencia del servidor, que sí es una frontera de cliente):

- ``policy.allowed_models`` / ``policy.allow_tools``: el modelo y las tools los elige
  Satellite, no un cliente externo. Aplicarlos aquí bloquearía el propio runtime.
- Los **esquemas de tools** no se tocan: renombrar o enmascarar nombres/parámetros rompería
  el tool calling. Sí se sanean los contenidos de los mensajes y los argumentos de las
  ``tool_calls`` del historial.

Si la política bloquea o no se puede cargar cuando el modo interno está activo, se lanza un
``ProviderError``: la petición **no se envía** y el error se explica, nunca se manda en
silencio ni se hace bypass.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from satellite.core.errors import ProviderError
from satellite.core.messages import LLMRequest, LLMResponse, Message
from satellite.providers.base import LLMProvider
from satellite.safe_proxy import config as sp_config
from satellite.safe_proxy.core import SafeProxyCore
from satellite.safe_proxy.sanitizer import Blocked, Vault

_CACHE: tuple[tuple, SafeProxyCore] | None = None


def reset_cache() -> None:
    """Olvida el núcleo cacheado (lo usan las pruebas; en producción lo invalida el mtime)."""
    global _CACHE
    _CACHE = None


class SafeProxyPolicyError(ProviderError):
    """La política del Safe Proxy bloqueó la petición: no se envió nada."""

    code = "safe_proxy_blocked"

    def __init__(self, reason: str, category: str) -> None:
        self.reason = reason
        self.category = category
        super().__init__(
            f"bloqueado por la política de Safe Proxy ({category}): {reason}. "
            "No se envió nada al proveedor."
        )


class SafeProxyUnavailableError(ProviderError):
    """El modo interno está activo pero no se pudo preparar su política.

    Es un fallo **cerrado**: si no se puede saber qué política aplicar, la petición no
    sale. Antes este caso se confundía con «modo apagado» y se enviaba sin sanear.
    """

    code = "safe_proxy_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "el Safe Proxy está activo pero no se pudo cargar su política. "
            "No se envió nada al proveedor."
        )


def _fingerprint(paths: list[Path]) -> tuple[int, ...]:
    out: list[int] = []
    for path in paths:
        try:
            out.append(path.stat().st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)


def load_internal_core(
    settings: "sp_config.SafeProxySettings | None" = None,
    *,
    data_dir: Path | str | None = None,
) -> SafeProxyCore | None:
    """Núcleo vivo del modo interno, o ``None`` si el modo interno no está activo.

    ``None`` (⇒ bypass claro, la petición sigue igual) cuando el modo no es ``internal`` o
    el interruptor interno está apagado. Si el modo está activo y la config no se puede
    cargar, la excepción se propaga (y el interceptor la convierte en fallo cerrado). El
    resultado se cachea por ``mtime`` de la config y del estado, de modo que cambiar la
    política en Ajustes se nota en la siguiente llamada sin releer los ficheros en cada una.
    """
    global _CACHE

    actual = settings if settings is not None else sp_config.load_settings()
    if actual.mode != sp_config.MODE_INTERNAL or not actual.internal_enabled:
        return None

    base = Path(data_dir) if data_dir is not None else sp_config.default_data_dir()
    state_path = base / "state.json"
    # La clave incluye la carpeta: dos host distintos con mtimes iguales no se confunden.
    vigencia: tuple = (str(base), *_fingerprint([base / "config.json", state_path]))
    if _CACHE is not None and _CACHE[0] == vigencia:
        return _CACHE[1]

    core = SafeProxyCore(_load_base_config(base), data_dir=base)
    core.load_state()
    _CACHE = (vigencia, core)
    return core


def _load_base_config(base: Path) -> dict:
    """Config base del modo interno (la misma que usa el servidor).

    Si el fichero no existe se usan los defaults (primera ejecución). Si existe pero no
    se puede leer o no es un objeto JSON, se **falla cerrado**: no se sustituye en
    silencio por una política distinta a la que el usuario configuró.
    """
    from satellite.safe_proxy.defaults import DEFAULT_CONFIG

    path = base / "config.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return json.loads(json.dumps(DEFAULT_CONFIG))
    except OSError as exc:
        raise ValueError("config.json del Safe Proxy ilegible") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("config.json del Safe Proxy inválido") from exc
    if not isinstance(data, dict) or not data:
        raise ValueError("config.json del Safe Proxy inválido")
    return data


def _sanitize_arguments(arguments: dict, core: SafeProxyCore, vault: Vault) -> dict:
    """Sanea los argumentos de una ``tool_call`` reutilizando el vault de la petición."""
    if not arguments:
        return arguments
    try:
        clean = core.sanitizer.run([json.dumps(arguments)], vault)[0]
        restored = json.loads(clean)
    except Blocked:
        raise
    except (ValueError, TypeError) as exc:
        raise Blocked(
            "no se pudieron sanear los argumentos de una tool_call", "policy"
        ) from exc
    if not isinstance(restored, dict):
        # Nunca se usan los argumentos originales: si el saneado cambió la forma, no
        # podemos garantizar que el contenido sensible se haya eliminado.
        raise Blocked("los argumentos de una tool_call no se pudieron sanear", "policy")
    return restored


def sanitize_request(request: LLMRequest, core: SafeProxyCore) -> tuple[LLMRequest, Vault]:
    """Aplica la política al contenido saliente y devuelve la petición y su vault."""
    vault = Vault()
    contents: list[str] = []
    for message in request.messages:
        contents.append(message.content or "")
    clean, vault = core.sanitize_texts(contents)

    messages: list[Message] = []
    for message, texto in zip(request.messages, clean):
        actualizado = replace(message, content=texto) if texto != message.content else message
        if message.tool_calls:
            actualizado = replace(
                actualizado,
                tool_calls=tuple(
                    replace(call, arguments=_sanitize_arguments(call.arguments, core, vault))
                    for call in message.tool_calls
                ),
            )
        messages.append(actualizado)
    return replace(request, messages=tuple(messages)), vault


def restore_response(response: LLMResponse, vault: Vault) -> LLMResponse:
    """Restaura los marcadores que el modelo haya devuelto (contenido y argumentos)."""
    content = vault.restore(response.content) if response.content else response.content
    tool_calls = response.tool_calls
    if tool_calls:
        tool_calls = tuple(
            replace(
                call,
                arguments=(
                    json.loads(vault.restore(json.dumps(call.arguments)))
                    if call.arguments
                    else call.arguments
                ),
            )
            for call in tool_calls
        )
    if content == response.content and tool_calls is response.tool_calls:
        return response
    return replace(response, content=content, tool_calls=tool_calls)


class SanitizingProvider(LLMProvider):
    """Decorador de proveedor: sanea lo que sale y restaura lo que vuelve (modo interno).

    Consulta el modo **en cada llamada**: activar o desactivar el proxy en Ajustes surte
    efecto sin rearmar el runtime. Si el modo interno no está activo, delega tal cual (bypass).
    """

    def __init__(self, inner: LLMProvider, core_loader=None) -> None:
        self.inner = inner
        self._load_core = core_loader or load_internal_core

    def __getattr__(self, item):
        # Delegación de cualquier atributo propio del proveedor (p. ej. ``name``).
        return getattr(self.inner, item)

    @property
    def reports_usage(self) -> bool:
        return bool(getattr(self.inner, "reports_usage", True))

    @property
    def automatic_cache(self) -> bool:
        return bool(getattr(self.inner, "automatic_cache", False))

    def cache_strategy(self):
        return self.inner.cache_strategy()

    def core(self) -> SafeProxyCore | None:
        """Núcleo activo, o ``None`` sólo si el modo interno está realmente apagado.

        Cualquier fallo al cargar la política cuando el modo está activo es fail-closed:
        se lanza ``SafeProxyUnavailableError`` y la petición no se envía. Devolver
        ``None`` aquí equivaldría a hacer bypass sin sanear.
        """
        try:
            core = self._load_core()
        except Exception as exc:  # noqa: BLE001 — se convierte en fallo cerrado
            raise SafeProxyUnavailableError() from exc
        if core is None:
            return None
        try:
            if not core.routing_enabled():
                return None
        except Exception as exc:  # noqa: BLE001 — sin estado fiable, no se envía
            raise SafeProxyUnavailableError() from exc
        return core

    async def complete(self, request: LLMRequest) -> LLMResponse:
        core = self.core()
        if core is None:
            return await self.inner.complete(request)  # bypass claro: nada se toca
        try:
            limpia, vault = sanitize_request(request, core)
        except Blocked as exc:
            core.audit(action="internal_blocked", category=exc.category)
            raise SafeProxyPolicyError(exc.reason, exc.category) from exc
        response = await self.inner.complete(limpia)
        return restore_response(response, vault)


def sanitize_provider(provider: LLMProvider, core_loader=None) -> LLMProvider:
    """Envuelve el proveedor para el modo interno (idempotente)."""
    if isinstance(provider, SanitizingProvider):
        return provider
    return SanitizingProvider(provider, core_loader=core_loader)


__all__ = [
    "SafeProxyPolicyError",
    "SafeProxyUnavailableError",
    "SanitizingProvider",
    "load_internal_core",
    "reset_cache",
    "restore_response",
    "sanitize_provider",
    "sanitize_request",
]
