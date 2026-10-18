"""Servicio Safe Proxy (app ASGI): el mismo núcleo, expuesto en loopback.

El saneado, la política y el estado viven en :class:`SafeProxyCore` (compartido con el modo
``internal``). Aquí sólo se añade la parte de red: la app FastAPI, la autenticación del
acceso y la API de control local. Sin cambios de comportamiento respecto al proxy original:
bloquea → ofusca → enmascara → reenvía → restaura → audita, siempre fail-closed.

Seguridad:
  - Escucha sólo en **loopback** (el host se fuerza a 127.0.0.1).
  - Token de acceso (``PROXY_TOKEN``) y token de control (``PROXY_CONTROL_TOKEN``) **separados**;
    el de control se compara en tiempo constante y **nunca** se devuelve.
  - Las respuestas van **redactadas**: sólo nombres de env y booleanos, jamás valores.
  - ``PATCH`` limitado a la allowlist real; el estado se persiste aparte, sin secretos.
  - Sin peticiones salientes más allá del upstream configurado y sin seguir redirects.

Origen ``safe-proxy/safe_proxy.py`` (sin licencia declarada; ver ``NOTICE.md``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from satellite.safe_proxy.config import SafeProxyConfigError, validate_config_patch
from satellite.safe_proxy.core import CONTROL_MIN_LEN, SafeProxyCore, StateError
from satellite.safe_proxy.sanitizer import Blocked, Vault, restore_obj

PASS_KEYS = (
    "model", "temperature", "top_p", "max_tokens", "stop",
    "presence_penalty", "frequency_penalty", "seed", "response_format",
)
ALLOWED_ROLES = {"system", "user", "assistant"}


class SafeProxyService:
    """Servicio embebido: la app ASGI sobre el núcleo compartido."""

    def __init__(
        self,
        config: dict | None = None,
        *,
        access_token: str,
        control_token: str,
        data_dir: Path | str,
        upstream_api_key: str = "",
        loopback_only: bool = True,
    ) -> None:
        if len(str(access_token or "")) < CONTROL_MIN_LEN:
            raise ValueError(f"el token de acceso debe tener al menos {CONTROL_MIN_LEN} caracteres")
        if len(str(control_token or "")) < CONTROL_MIN_LEN:
            raise ValueError(f"el token de control debe tener al menos {CONTROL_MIN_LEN} caracteres")

        self.access_token = str(access_token)
        self.control_token = str(control_token)

        cfg = json.loads(json.dumps(config)) if config is not None else None
        self.core = SafeProxyCore(cfg, data_dir=data_dir, upstream_api_key=upstream_api_key)
        # El servicio embebido nunca escucha fuera de loopback.
        if loopback_only:
            self.core.cfg.setdefault("server", {})["host"] = "127.0.0.1"

        self.app = self._build_app()
        self.load_state()

    # ------------------------------------------------------------------ #
    # Acceso al núcleo (compatibilidad: los mismos nombres de antes)
    # ------------------------------------------------------------------ #
    @property
    def cfg(self) -> dict:
        return self.core.cfg

    @property
    def data_dir(self) -> Path:
        return self.core.data_dir

    @property
    def state_path(self) -> Path:
        return self.core.state_path

    @property
    def audit_path(self) -> Path:
        return self.core.audit_path

    @property
    def max_body(self) -> int:
        return self.core.max_body

    def routing_enabled(self) -> bool:
        return self.core.routing_enabled()

    def set_routing(self, enabled: bool) -> None:
        self.core.set_routing(enabled)

    def load_state(self) -> None:
        self.core.load_state()

    def apply_patch(self, changes: dict) -> None:
        self.core.apply_patch(changes)

    def persist_enabled(self, enabled: bool) -> None:
        self.core.persist_enabled(enabled)

    def audit(self, **fields) -> None:
        self.core.audit(**fields)

    def _rebuild_sanitizer(self) -> None:
        self.core._rebuild_sanitizer()

    def api_key_configured(self) -> bool:
        return self.core.api_key_configured()

    def redacted_config(self) -> dict:
        data = self.core.redacted_config()
        data["server"]["control_token_configured"] = bool(self.control_token)
        return data

    def status_payload(self) -> dict:
        data = self.core.status_payload()
        data["control"] = {
            "available": bool(self.control_token),
            "token_configured": bool(self.control_token),
            "token_source": "embedded",
        }
        return data

    # ------------------------------------------------------------------ #
    # Pipeline de la petición
    # ------------------------------------------------------------------ #
    @staticmethod
    def _content_to_text(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                else:
                    raise Blocked("contenido no textual (imagenes/archivos) no permitido", "policy")
            return "\n".join(chunks)
        raise Blocked("formato de contenido no soportado", "policy")

    def build_payload(self, body, vault: Vault) -> dict:
        pol = self.cfg.get("policy", {})
        if not isinstance(body, dict):
            raise Blocked("cuerpo invalido", "policy")
        if body.get("model") not in (pol.get("allowed_models") or []):
            raise Blocked("modelo no permitido", "policy")
        if (body.get("tools") or body.get("functions")) and not pol.get("allow_tools", False):
            raise Blocked("tools/functions deshabilitados por politica", "policy")
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            raise Blocked("messages vacio o invalido", "policy")

        texts = []
        for m in msgs:
            if not isinstance(m, dict) or m.get("role") not in ALLOWED_ROLES:
                raise Blocked("rol de mensaje no permitido", "policy")
            if m.get("tool_calls") or m.get("function_call"):
                raise Blocked("tool_calls deshabilitados por politica", "policy")
            texts.append(self._content_to_text(m.get("content")))

        clean = self.core.sanitizer.run(texts, vault)
        payload = {k: body[k] for k in PASS_KEYS if k in body}
        payload["messages"] = [{"role": m["role"], "content": c} for m, c in zip(msgs, clean)]
        payload["stream"] = False
        return payload

    async def _call_upstream(self, payload: dict) -> tuple[int, dict]:
        import httpx

        if not self.api_key_configured():
            return 500, {"error": {"message": "Falta la API key del proveedor en el entorno del proxy"}}
        base = str(self.cfg.get("upstream", {}).get("base_url", "")).rstrip("/")
        url = base + "/chat/completions"
        timeout = float(self.cfg.get("upstream", {}).get("timeout_seconds", 180))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.core.upstream_api_key}"},
            )
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"error": {"message": r.text[:500]}}

    @staticmethod
    def _sse(obj) -> str:
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

    def _to_sse(self, resp: dict, headers: dict) -> StreamingResponse:
        base = {
            "id": resp.get("id", "chatcmpl-safe-proxy"),
            "object": "chat.completion.chunk",
            "created": resp.get("created", int(time.time())),
            "model": resp.get("model"),
        }

        def gen():
            for ch in resp.get("choices", []):
                msg = ch.get("message") or {}
                idx = ch.get("index", 0)
                delta = {"role": "assistant", "content": msg.get("content") or ""}
                if msg.get("reasoning_content"):
                    delta["reasoning_content"] = msg["reasoning_content"]
                yield self._sse({**base, "choices": [{"index": idx, "delta": delta, "finish_reason": None}]})
                yield self._sse(
                    {**base, "choices": [{"index": idx, "delta": {}, "finish_reason": ch.get("finish_reason", "stop")}]}
                )
            if resp.get("usage"):
                yield self._sse({**base, "choices": [], "usage": resp["usage"]})
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)

    # ------------------------------------------------------------------ #
    # App ASGI
    # ------------------------------------------------------------------ #
    def _build_app(self) -> FastAPI:
        app = FastAPI(title="safe-proxy", docs_url=None, redoc_url=None, openapi_url=None)

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            return await self._handle(request, preview=False)

        @app.post("/preview")
        async def preview(request: Request):
            return await self._handle(request, preview=True)

        @app.get("/health")
        async def health():
            return {"ok": True}

        app.add_api_route("/control/status", self.control_status, methods=["GET"])
        app.add_api_route("/control/config", self.control_get_config, methods=["GET"])
        app.add_api_route("/control/config", self.control_patch_config, methods=["PATCH"])
        app.add_api_route("/control/enable", self.control_enable, methods=["POST"])
        app.add_api_route("/control/disable", self.control_disable, methods=["POST"])
        return app

    # -- auth y guardas -------------------------------------------------- #
    def _auth_access(self, request: Request) -> None:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), self.access_token.encode()):
            raise HTTPException(status_code=401, detail="No autorizado")

    @staticmethod
    def _is_loopback(host: str | None) -> bool:
        if not host:
            return False
        h = host.strip().strip("[]").lower()
        if h in ("localhost", "::1", "0:0:0:0:0:0:0:1"):
            return True
        return h.startswith("127.")

    def _client_is_loopback(self, request: Request) -> bool:
        client = request.client
        return client is None or self._is_loopback(client.host)

    def _control_guard(self, request: Request) -> None:
        if not self.control_token:
            raise HTTPException(status_code=503, detail="API de control deshabilitada.")
        if not self._client_is_loopback(request):
            raise HTTPException(
                status_code=403, detail="La API de control solo acepta peticiones desde loopback."
            )
        supplied = request.headers.get("x-control-token", "")
        auth = request.headers.get("authorization", "")
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:]
        if not hmac.compare_digest(supplied.encode(), self.control_token.encode()):
            raise HTTPException(status_code=401, detail="No autorizado")

    @staticmethod
    def _err(status: int, message: str, kind: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {"message": message, "type": kind}})

    # -- handlers -------------------------------------------------------- #
    async def _handle(self, request: Request, preview: bool):
        self._auth_access(request)
        if not self.routing_enabled():
            return self._err(
                503,
                "El routing del proxy esta deshabilitado; activalo con POST /control/enable.",
                "routing_disabled",
            )
        raw = await request.body()
        if len(raw) > self.max_body:
            return self._err(413, "Cuerpo demasiado grande", "too_large")
        try:
            body = json.loads(raw)
        except ValueError:
            return self._err(400, "JSON invalido", "bad_request")

        rid = secrets.token_hex(6)
        vault = Vault()
        try:
            payload = self.build_payload(body, vault)
        except Blocked as e:
            self.audit(rid=rid, action="blocked", category=e.category)
            return self._err(403, f"Bloqueado por politica: {e.reason}. No se envio nada.", "policy_blocked")
        except Exception as e:  # fail-closed: ante cualquier fallo no se envía nada
            self.audit(rid=rid, action="sanitize_error", error=type(e).__name__)
            return self._err(500, "Fallo al sanear la peticion; no se envio nada.", "sanitize_failed")

        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        headers = {"X-Safe-Proxy-Stats": json.dumps(vault.stats)}

        if preview:
            self.audit(rid=rid, action="preview", stats=vault.stats, sha256=digest)
            return JSONResponse({"sanitized_payload": payload, "stats": vault.stats}, headers=headers)

        try:
            status, data = await self._call_upstream(payload)
        except Exception as e:
            self.audit(rid=rid, action="upstream_error", error=type(e).__name__, stats=vault.stats, sha256=digest)
            return self._err(502, "No se pudo contactar al proveedor", "upstream_unreachable")

        self.audit(
            rid=rid, action="forwarded", status=status, stats=vault.stats, sha256=digest,
            model=payload["model"],
        )
        data = restore_obj(data, vault)
        if status == 200 and body.get("stream"):
            return self._to_sse(data, headers)
        return JSONResponse(status_code=status, content=data, headers=headers)

    async def control_status(self, request: Request):
        self._control_guard(request)
        return self.status_payload()

    async def control_get_config(self, request: Request):
        self._control_guard(request)
        return self.redacted_config()

    async def control_patch_config(self, request: Request):
        self._control_guard(request)
        raw = await request.body()
        if len(raw) > self.max_body:
            raise HTTPException(status_code=413, detail="Cuerpo demasiado grande")
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="JSON invalido")
        try:
            changes = validate_config_patch(body)
        except SafeProxyConfigError as e:
            raise HTTPException(status_code=400, detail=e.message) from e
        if not changes:
            raise HTTPException(status_code=400, detail="no se proporciono ningun cambio valido")
        try:
            self.apply_patch(changes)
        except (OSError, StateError) as e:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo persistir el estado ({type(e).__name__}); cambios no aplicados.",
            ) from e
        self.audit(rid=secrets.token_hex(6), action="control_patch", sections=sorted(changes))
        return {"ok": True, "config": self.redacted_config()}

    async def control_enable(self, request: Request):
        self._control_guard(request)
        try:
            self.persist_enabled(True)
        except (OSError, StateError) as e:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo persistir el estado ({type(e).__name__}); 'enabled' no cambio.",
            ) from e
        self.set_routing(True)
        self.audit(rid=secrets.token_hex(6), action="control_enable")
        return {"enabled": True}

    async def control_disable(self, request: Request):
        self._control_guard(request)
        try:
            self.persist_enabled(False)
        except (OSError, StateError) as e:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo persistir el estado ({type(e).__name__}); 'enabled' no cambio.",
            ) from e
        self.set_routing(False)
        self.audit(rid=secrets.token_hex(6), action="control_disable")
        return {"enabled": False}
