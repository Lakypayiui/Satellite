"""Aplicación FastAPI del backend de escritorio (sólo ``localhost``, siempre con token)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from satellite.desktop import require_dependencies
from satellite.desktop.api import build_router
from satellite.desktop.auth import make_dependency
from satellite.desktop.backend import BackendError, DesktopBackend


def create_app(backend: DesktopBackend) -> FastAPI:
    """Construye la app; sin OpenAPI expuesto (no hace falta y no se regala)."""
    require_dependencies()
    app = FastAPI(
        title="Satellite Desktop",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(build_router(backend, make_dependency(backend.token)))

    @app.get("/api/health")
    def health() -> dict:
        """Vivo. Sin token y sin datos: sólo dice que el proceso responde."""
        return {"ok": True}

    @app.exception_handler(BackendError)
    async def backend_error(_request: Request, exc: BackendError) -> JSONResponse:
        """Un error del backend se cuenta con código y mensaje, nunca como 500 mudo."""
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Un cuerpo mal formado usa el mismo formato: la app lee siempre ``error``."""
        problemas = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}: {item.get('msg', '')}"
            for item in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": problemas or "petición inválida",
                }
            },
        )

    return app
