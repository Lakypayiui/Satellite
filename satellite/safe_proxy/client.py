"""Cliente HTTP de la API de control local de Safe Proxy.

Sólo habla con **loopback** (la URL se revalida aquí antes de cada petición), **no sigue
redirecciones** y usa un **timeout** corto: nada de SSRF ni de remotos. El token de control
viaja en la cabecera ``X-Control-Token`` y no se registra en ningún sitio.
"""

from __future__ import annotations

import httpx

from satellite.safe_proxy.config import SafeProxyConfigError, normalize_base_url

DEFAULT_TIMEOUT = 5.0


class SafeProxyError(RuntimeError):
    """Fallo al hablar con el proxy; ``code``/``status`` ayudan a la interfaz."""

    def __init__(self, code: str, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SafeProxyClient:
    """Cliente mínimo del control local: status, config, PATCH y enable/disable."""

    def __init__(self, base_url: str, control_token: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        # Revalida en cada construcción: nunca se acepta una URL no-loopback.
        self.base_url = normalize_base_url(base_url)
        self._token = str(control_token or "")
        self._timeout = max(0.5, float(timeout))

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                response = client.request(
                    method,
                    url,
                    headers={"X-Control-Token": self._token},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise SafeProxyError(
                "safe_proxy_offline",
                f"no se pudo contactar el proxy en {self.base_url} ({type(exc).__name__})",
            ) from exc

        if 300 <= response.status_code < 400:
            raise SafeProxyError(
                "safe_proxy_redirect",
                "el proxy intentó redirigir la petición; no se sigue (SSRF)",
                response.status_code,
            )
        if response.status_code == 401:
            raise SafeProxyError(
                "safe_proxy_unauthorized",
                "el token de control fue rechazado por el proxy",
                401,
            )
        if response.status_code >= 400:
            detail = _detail(response)
            raise SafeProxyError(
                "safe_proxy_error", detail or f"el proxy respondió {response.status_code}",
                response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise SafeProxyError(
                "safe_proxy_bad_response", "el proxy devolvió una respuesta no JSON"
            ) from exc
        if not isinstance(data, dict):
            raise SafeProxyError("safe_proxy_bad_response", "respuesta inesperada del proxy")
        return data

    def status(self) -> dict:
        """``GET /control/status``."""
        return self._request("GET", "/control/status")

    def config(self) -> dict:
        """``GET /control/config`` (ya viene redactada, sin secretos)."""
        return self._request("GET", "/control/config")

    def patch_config(self, changes: dict) -> dict:
        """``PATCH /control/config`` con la allowlist ya normalizada."""
        return self._request("PATCH", "/control/config", changes)

    def set_enabled(self, enabled: bool) -> dict:
        """``POST /control/enable`` o ``/control/disable``: routing real del proxy."""
        return self._request("POST", "/control/enable" if enabled else "/control/disable")


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()[:300]
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return ""


__all__ = ["SafeProxyClient", "SafeProxyError", "SafeProxyConfigError", "normalize_base_url"]
